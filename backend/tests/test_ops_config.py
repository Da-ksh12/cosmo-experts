"""Structural checks on the deployment files. These do NOT replace `docker compose config` / a real build (no container runtime was available when
they were written); they catch the mistakes that are cheap to catch: undefined secrets, extra published ports, inline credentials, missing files."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_only_the_reverse_proxy_publishes_ports():
    published = {n for n, s in compose["services"].items() if s.get("ports")}
    assert published == {"caddy"} and compose["services"]["caddy"]["ports"] == ["80:80", "443:443"]


def test_every_referenced_secret_is_defined_and_files_are_not_in_the_repo():
    defined = set(compose["secrets"])
    for name, svc in compose["services"].items():
        assert set(svc.get("secrets", [])) <= defined, name
    for s in compose["secrets"].values():
        assert s["file"].startswith("./secrets/")
    assert "secrets/" in (ROOT / ".gitignore").read_text()


def test_backend_reads_secrets_from_files_not_inline_values():
    env = compose["services"]["backend"]["environment"]
    for k in ("COSMO_SECRET", "SUPABASE_SERVICE_ROLE_KEY", "COSMO_BACKUP_KEY", "COSMO_METRICS_TOKEN", "COSMO_REDIS_URL"):
        assert k not in env and f"{k}_FILE" in env and env[f"{k}_FILE"].startswith("/run/secrets/")
    from app import secrets_env
    assert {k.removesuffix("_FILE") for k in env if k.endswith("_FILE")} <= set(secrets_env.ALLOWED)
    assert env["COSMO_MODE"] == "production" and env["COSMO_AUTH"] == "supabase" and env["COSMO_STORAGE"] == "supabase"
    assert env["COSMO_CORS"].startswith("https://")


def test_containers_are_hardened_and_state_is_on_volumes():
    for n in ("frontend", "backend"):
        s = compose["services"][n]
        assert s["cap_drop"] == ["ALL"] and "no-new-privileges:true" in s["security_opt"]
    assert "cosmo_data:/data" in compose["services"]["backend"]["volumes"]
    assert set(compose["services"]["prometheus"]["profiles"]) == {"monitoring"}
    assert compose["networks"]["cosmo"] in (None, {})                        # egress needed for Supabase: must not be an internal-only network


def test_referenced_config_files_exist():
    for svc in compose["services"].values():
        for v in svc.get("volumes", []):
            if isinstance(v, str) and v.startswith("./"):
                assert (ROOT / v.split(":")[0]).exists(), v
    for f in ("backend/Dockerfile", "frontend/Dockerfile", "ops/Caddyfile", "ops/Caddyfile.local", "ops/prometheus/prometheus.yml", "ops/prometheus/alerts.yml",
              "ops/prometheus/alertmanager.yml", "ops/systemd/cosmo-backup.timer", ".github/workflows/ci.yml", ".env.example"):
        assert (ROOT / f).is_file(), f


def test_dockerfiles_run_unprivileged_with_health_checks():
    be = (ROOT / "backend/Dockerfile").read_text()
    fe = (ROOT / "frontend/Dockerfile").read_text()
    assert re.search(r"^USER cosmo", be, re.M) and re.search(r"^USER node", fe, re.M)
    assert "HEALTHCHECK" in be and "HEALTHCHECK" in fe and "--workers 1" in be
    assert "COPY data" not in be and "COPY . ." not in be                          # models, databases and uploads never baked into the image


def test_alert_rules_reference_only_metrics_the_application_exports():
    from app import metrics
    exported = set(re.findall(r'"(cosmo_[a-z0-9_]+)"', Path(metrics.__file__).read_text(encoding="utf-8")))
    exported |= {"cosmo_http_requests_total", "cosmo_http_request_duration_seconds_bucket"}
    rules = yaml.safe_load((ROOT / "ops/prometheus/alerts.yml").read_text(encoding="utf-8"))
    used = set()
    for g in rules["groups"]:
        for r in g["rules"]:
            used |= set(re.findall(r"\bcosmo_[a-z0-9_]+", r["expr"]))
    assert used and used <= exported, used - exported


def test_ci_runs_the_security_and_quality_gates():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    assert set(ci["jobs"]) == {"secrets-scan", "backend", "frontend", "containers"}
    for needle in ("scan_secrets.py", "coverage report --fail-under=85", "tsc --noEmit", "npm audit", "hadolint", "docker build", "promtool check rules"):
        assert needle in text, needle
    assert ci["permissions"] == {"contents": "read"}
