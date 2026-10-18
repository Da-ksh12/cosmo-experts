"""Runtime configuration. Local dev mode needs no cloud credentials."""
import os
from pathlib import Path

from . import secrets_env

secrets_env.load()                       # NAME_FILE -> NAME for allow-listed secrets (Docker/K8s secrets), before anything reads them

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("COSMO_DATA_DIR", ROOT / "data"))
UPLOAD_DIR = DATA_DIR / "private_uploads"   # never served statically
DB_PATH = DATA_DIR / "cosmo.sqlite3"
MODEL_DIR = DATA_DIR / "models"
DATASET_DIR = DATA_DIR / "datasets"
for _d in (DATA_DIR, UPLOAD_DIR, MODEL_DIR, DATASET_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Local development mode: built-in users + HMAC session tokens. In production set
# SUPABASE_JWT_SECRET (see docs/DEPLOYMENT.md) and COSMO_MODE=production.
MODE = os.environ.get("COSMO_MODE", "local")
SECRET = os.environ.get("COSMO_SECRET", "dev-only-secret-change-me")
MAX_UPLOAD_BYTES = int(os.environ.get("COSMO_MAX_UPLOAD_MB", "15")) * 1024 * 1024
MAX_PDF_PAGES = int(os.environ.get("COSMO_MAX_PDF_PAGES", "40"))
AUDIO_RETENTION_SECONDS = 0  # raw audio is never retained
CONVERSATION_RETENTION_DAYS = int(os.environ.get("COSMO_CONV_RETENTION_DAYS", "30"))
CORS_ORIGINS = os.environ.get("COSMO_CORS", "http://localhost:3000").split(",")
