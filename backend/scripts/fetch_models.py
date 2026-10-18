"""Explicit, auditable model acquisition. Prints licence tags and commit hashes before downloading; never runs at request time.
Usage: python -m scripts.fetch_models [--all]   (default: embedding + speech models, both MIT)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from huggingface_hub import HfApi, snapshot_download  # noqa: E402

from app.config import MODEL_DIR  # noqa: E402

MODELS = [
    ("BAAI/bge-small-en-v1.5", "embed/bge-small-en-v1.5", ["*.json", "*.txt", "model.safetensors", "1_Pooling/*", "modules.json"]),
    ("Systran/faster-whisper-base", "stt/faster-whisper-base", None),
]
ALLOWED_LICENSES = {"license:mit", "license:apache-2.0", "license:bsd-3-clause"}

if __name__ == "__main__":
    api = HfApi()
    for repo, sub, patterns in MODELS:
        info = api.model_info(repo)
        lic = [t for t in info.tags if t.startswith("license:")]
        print(f"{repo}: {lic} commit {info.sha[:10]}")
        if not set(lic) & ALLOWED_LICENSES:
            print("  refusing: licence not in the allowed set; review manually")
            continue
        dest = MODEL_DIR / sub
        snapshot_download(repo, local_dir=str(dest), allow_patterns=patterns)
        (dest / "PROVENANCE.json").write_text(json.dumps({"source": f"https://huggingface.co/{repo}", "commit": info.sha, "license_tags": lic}, indent=1))
        print("  saved to", dest)
