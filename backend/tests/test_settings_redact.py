"""Settings validation (fail closed in production) and secret/personal-data redaction. Credential-shaped fixtures are assembled at run time."""
import io
import json
import logging

import pytest

from app import logging_setup, redact
from app.settings import ConfigError, Settings

JWT = ".".join(["eyJ" + "a" * 20, "eyJ" + "b" * 20, "c" * 24])
SBP = "sbp_" + "0123456789abcdef" * 3
PROD = {"COSMO_MODE": "production", "COSMO_DB": "postgres", "COSMO_DATABASE_URL": "postgresql://cosmo_app:pw@db.example.org:5432/cosmo?sslmode=require",
        "COSMO_COOKIE_SECURE": "1", "COSMO_AUTH": "supabase", "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_ANON_KEY": "anon", "COSMO_ADMIN_CLINICAL_ACCESS": "none", "COSMO_DB_CONTEXT_SECRET": "c" * 40}


# ---------------------------------------------------------------- redaction
@pytest.mark.parametrize("text,gone", [
    (f"token {SBP} leaked", SBP), (f"jwt={JWT}", JWT), (f"Authorization: Bearer {JWT}", JWT),
    ("could not connect to postgresql://cosmo_app:S3cretPw!@db.host:5432/x", "S3cretPw!"), ("password=hunter2 user=a", "hunter2"), ('{"api_key": "abc123xyz"}', "abc123xyz"),
    ("Cookie: cosmo_rt=abcdef123456; other=1", "abcdef123456"), ("-----BEGIN " + "PRIVATE KEY-----\nMIIabc\n-----END " + "PRIVATE KEY-----", "MIIabc"),
])
def test_secrets_are_redacted_from_text(text, gone):
    out = redact.redact(text)
    assert gone not in out and "REDACTED" in out


def test_email_addresses_are_masked_but_ordinary_text_is_untouched():
    assert redact.redact("user jane.doe@example.org signed in") == "user ja***@example.org signed in"
    assert redact.redact("Ferritin 8 ng/mL at page 2") == "Ferritin 8 ng/mL at page 2"


def test_redact_obj_hides_sensitive_keys_and_omits_patient_content():
    o = redact.redact_obj({"user": "u1", "password": "x", "headers": {"Authorization": "Bearer abc", "X-Id": "1"}, "text": "Ferritin 8 low", "items": [{"token": "t"}], "n": 3,
                           "note": f"see {SBP}", "file": b"\x00" * 10})
    assert o["password"] == "[REDACTED]" and o["headers"]["Authorization"] == "[REDACTED]" and o["items"][0]["token"] == "[REDACTED]"
    assert o["text"] == "[14 chars omitted]" and o["file"] == "[10 bytes omitted]"
    assert SBP not in json.dumps(o) and o["n"] == 3 and o["user"] == "u1"


def test_safe_exc_redacts_and_truncates():
    e = RuntimeError(f"connect failed password=hunter2 {SBP} " + "x" * 1000)
    s = redact.safe_exc(e, 120)
    assert s.startswith("RuntimeError:") and "hunter2" not in s and SBP not in s and len(s) <= 140


def _capture():
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging_setup.JsonFormatter())
    h.addFilter(logging_setup.RedactingFilter())
    lg = logging.getLogger("cosmo.test." + str(id(buf)))
    lg.handlers = [h]
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    return lg, buf


def test_log_records_are_json_carry_the_request_id_and_are_redacted():
    lg, buf = _capture()
    tok = logging_setup.request_id_var.set("req-123")
    try:
        lg.info("GET /api/v1/events?token=%s HTTP/1.1 200", JWT)                              # uvicorn access-log shape
        lg.warning("db error: %s", "could not connect to postgresql://u:SuperSecret1@h/db")
        logging_setup.log_fields(lg, logging.INFO, "upload", user="u1", password="pw", text="private report text")
        try:
            raise ValueError(f"bad {SBP}")
        except ValueError:
            lg.exception("failed")
    finally:
        logging_setup.request_id_var.reset(tok)
    lines = [json.loads(x) for x in buf.getvalue().splitlines()]
    assert len(lines) == 4 and all(x["request_id"] == "req-123" and x["level"] and x["ts"].endswith("Z") for x in lines)
    blob = buf.getvalue()
    for secret in (JWT, "SuperSecret1", SBP, "private report text", '"pw"'):
        assert secret not in blob, secret
    assert lines[2]["fields"]["password"] == "[REDACTED]" and lines[2]["fields"]["text"].startswith("[")


def test_a_failing_redaction_never_breaks_logging(monkeypatch):
    lg, buf = _capture()

    def boom(s):
        raise RuntimeError("boom")
    monkeypatch.setattr(logging_setup, "redact", boom)
    lg.info("hello")
    assert "could not be redacted" in buf.getvalue()


# ---------------------------------------------------------------- settings
def test_local_defaults_are_safe_and_explicit():
    s = Settings.from_env({})
    assert (s.mode, s.db_backend, s.migration_mode, s.auth_mode, s.cookie_secure, s.cookie_samesite) == ("local", "sqlite", "apply", "local", False, "strict")
    assert s.problems() == []                                                                    # production rules do not apply outside production


@pytest.mark.parametrize("env,needle", [({"COSMO_MODE": "staging"}, "COSMO_MODE"), ({"COSMO_DB": "mysql"}, "COSMO_DB"), ({"COSMO_DB_POOL_MAX": "x"}, "integer"),
                                        ({"COSMO_DB_POOL_MIN": "5", "COSMO_DB_POOL_MAX": "2"}, "must not exceed"), ({"COSMO_DB_POOL_MAX": "0"}, "between"),
                                        ({"COSMO_MIGRATION_MODE": "yolo"}, "COSMO_MIGRATION_MODE"), ({"COSMO_COOKIE_SAMESITE": "none"}, "COSMO_COOKIE_SAMESITE")])
def test_invalid_values_are_rejected_with_a_clear_redacted_message(env, needle):
    with pytest.raises(ConfigError, match=needle):
        Settings.from_env(env)


def test_describe_never_contains_a_secret():
    s = Settings.from_env({**PROD, "SUPABASE_SERVICE_ROLE_KEY": JWT})
    d = json.dumps(s.describe())
    assert "pw@" not in d and ":pw" not in d and JWT not in d
    assert s.describe()["database"] == "db.example.org:5432/cosmo" and s.describe()["supabase_service_key_set"] is True


def test_a_correct_production_configuration_has_no_database_problems():
    assert Settings.from_env(PROD).problems() == []


@pytest.mark.parametrize("change,needle", [
    ({"COSMO_DB": ""}, "COSMO_DB=postgres"), ({"COSMO_DB": "sqlite"}, "COSMO_DB=postgres"),
    ({"COSMO_DATABASE_URL": ""}, "postgresql:// URL"), ({"COSMO_DATABASE_URL": "mysql://u:p@h/d"}, "postgresql:// URL"),
    ({"COSMO_DATABASE_URL": "postgresql://u:p@db.example.org/d"}, "require TLS"),
    ({"COSMO_MIGRATION_MODE": "apply"}, "startup is not allowed"), ({"COSMO_MIGRATION_MODE": "off"}, "version check"),
    ({"COSMO_COOKIE_SECURE": "0"}, "COSMO_COOKIE_SECURE"), ({"SUPABASE_ANON_KEY": ""}, "SUPABASE_ANON_KEY"),
    ({"COSMO_ADMIN_CLINICAL_ACCESS": ""}, "ADMIN_CLINICAL_ACCESS"), ({"COSMO_ADMIN_CLINICAL_ACCESS": "maybe"}, "ADMIN_CLINICAL_ACCESS"),
])
def test_each_unsafe_production_database_or_session_setting_is_reported(change, needle):
    probs = Settings.from_env({**PROD, **change}).problems()
    assert any(needle in p for p in probs), probs


def test_explicit_acknowledgements_and_localhost_are_honoured():
    assert Settings.from_env({**PROD, "COSMO_DB": "sqlite", "COSMO_ALLOW_SQLITE_IN_PRODUCTION": "1"}).problems() == []
    assert Settings.from_env({**PROD, "COSMO_MIGRATION_MODE": "apply", "COSMO_ALLOW_STARTUP_MIGRATIONS": "1"}).problems() == []
    assert Settings.from_env({**PROD, "COSMO_DATABASE_URL": "postgresql://u:p@localhost/d"}).problems() == []
    assert Settings.from_env({**PROD, "COSMO_ADMIN_CLINICAL_ACCESS": "read"}).problems() == []


def test_the_postgres_url_never_appears_in_a_problem_message():
    probs = Settings.from_env({**PROD, "COSMO_DATABASE_URL": "postgresql://u:TopSecretPw@db.example.org/d"}).problems()
    assert probs and "TopSecretPw" not in " ".join(probs)
