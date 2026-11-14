import json
import os
from importlib.resources import files
from pathlib import Path

MODEL_SPEC = json.loads((files("cf") / "data" / "model.json").read_text(encoding="utf-8"))
MODEL_NAME = os.environ.get("CF_MODEL", MODEL_SPEC["model"])
MODEL_REVISION = os.environ.get("CF_MODEL_REVISION", MODEL_SPEC["revision"])
MODEL_PRECISION = MODEL_SPEC["precision"]
EMBEDDING_DIM = int(os.environ.get("CF_EMBEDDING_DIM", MODEL_SPEC["dimensions"]))
MAX_TOKENS = MODEL_SPEC["max_tokens"]
NUM_THREADS = int(os.environ.get("CF_THREADS", 2))
DEFAULT_BATCH_SIZE = int(os.environ.get("CF_BATCH_SIZE", 32))
DEFAULT_TOP_K = int(os.environ.get("CF_TOP", 7))
DEFAULT_MAX_TOP_K = int(os.environ.get("CF_MAX_TOP", 50))

DATA_DIR = Path(os.environ.get("CF_DATA_DIR", str(files("cf") / "data" / "commands")))
DB_DIR = Path(os.environ.get("CF_DB_DIR", str(Path.home() / ".local" / "share" / "cf-ru")))
DB_PATH = DB_DIR / os.environ.get("CF_DB_NAME", "cf.db")
ONNX_DIR = Path(os.environ.get("CF_MODEL_DIR", str(DB_DIR / "onnx")))
