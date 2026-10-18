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

# Exercise the SHIPPED configuration: use the real downloaded models (not the empty temp data dir) when they exist.
_real = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "models")
if os.path.isfile(os.path.join(_real, "embed", "bge-small-en-v1.5", "model.safetensors")):
    os.environ["COSMO_EMBED_MODEL_DIR"] = os.path.join(_real, "embed", "bge-small-en-v1.5")
if os.path.isfile(os.path.join(_real, "stt", "faster-whisper-base", "model.bin")):
    os.environ["COSMO_WHISPER_MODEL_DIR"] = os.path.join(_real, "stt", "faster-whisper-base")
