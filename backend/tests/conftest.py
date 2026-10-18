import os
import sys
import tempfile

os.environ["COSMO_RATE_LIMIT"] = "off"
os.environ["COSMO_DATA_DIR"] = tempfile.mkdtemp(prefix="cosmo_test_")
# the data directory (database, uploads, checkpoints, downloaded fixtures) can reach hundreds of MB: remove it when the test process exits
import atexit
import shutil
atexit.register(shutil.rmtree, os.environ["COSMO_DATA_DIR"], True)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# COSMO_TEST_DB=postgres runs the application tests against a disposable local PostgreSQL (see tests/pg_harness.py) instead of SQLite. The application connects as the
# restricted cosmo_app role; migrations are applied with the owner credential, exactly as in a real deployment.
if os.environ.get("COSMO_TEST_DB") == "postgres":
    from tests import pg_harness
    _srv = pg_harness.get_server()
    if _srv is None:
        raise RuntimeError("COSMO_TEST_DB=postgres was requested but no PostgreSQL binaries were found (set COSMO_PG_BIN)")
    _db = _srv.new_database("suite")
    os.environ.update(COSMO_DB="postgres", COSMO_DATABASE_URL=_srv.url(_db, "cosmo_app"), COSMO_MIGRATION_DATABASE_URL=_srv.url(_db), COSMO_MIGRATION_MODE="apply",
                      COSMO_DB_CONTEXT_SECRET="test-context-secret-" + "x" * 32)

# Exercise the SHIPPED configuration: use the real downloaded models (not the empty temp data dir) when they exist.
_real = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "models")
if os.path.isfile(os.path.join(_real, "embed", "bge-small-en-v1.5", "model.safetensors")):
    os.environ["COSMO_EMBED_MODEL_DIR"] = os.path.join(_real, "embed", "bge-small-en-v1.5")
if os.path.isfile(os.path.join(_real, "stt", "faster-whisper-base", "model.bin")):
    os.environ["COSMO_WHISPER_MODEL_DIR"] = os.path.join(_real, "stt", "faster-whisper-base")


import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _tests_run_database_fixtures_as_the_system():
    """Test fixtures and assertions that touch the database directly (outside any request) act as the server itself. HTTP requests are NOT affected: the middleware
    resets every request to 'no identity' and only the authentication dependency establishes one, so a route that forgot to authenticate still gets nothing."""
    from app.dbcore import context
    tok = context.set_user("", "system")
    yield
    context.reset(tok)
