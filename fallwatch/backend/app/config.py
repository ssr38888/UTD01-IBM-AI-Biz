"""All settings come from environment variables so the same image runs on a laptop and on LinuxONE.

Secrets (access keys) are never written in code. They come from the environment or from a
`.env` file at the repo root that `python scripts/make_keys.py` creates (git-ignored, owner-only).
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root (holds phone/ and viewer/)


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines; real environment variables always win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(Path(os.environ.get("FALLWATCH_ENV_FILE", ROOT / ".env")))


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


# --- database ---------------------------------------------------------------
# On LinuxONE: postgresql+psycopg://fallwatch:<pw>@db:5432/fallwatch
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'fallwatch.db'}")

# --- image storage ----------------------------------------------------------
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")  # "local" (folder on this box) or "s3"
LOCAL_STORAGE_DIR = Path(os.environ.get("LOCAL_STORAGE_DIR", ROOT / "data" / "objects"))
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "") or None  # None = AWS default; set for IBM COS / Ceph
S3_BUCKET = os.environ.get("S3_BUCKET", "fallwatch-evidence")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
# >0 turns on write-once (Object Lock, compliance mode) for this many days:
# nobody, including admins, can delete or overwrite evidence before then.
S3_RETENTION_DAYS = _int("S3_RETENTION_DAYS", 0)

# --- access keys ------------------------------------------------------------
# Phones send X-Device-Key, the viewer sends X-Viewer-Key (headers only, never in URLs).
# No defaults: the server refuses to start until real keys are set (see check_keys).
DEVICE_KEYS = _list("DEVICE_KEYS")
VIEWER_KEYS = _list("VIEWER_KEYS")
MIN_KEY_LENGTH = 24


def check_keys() -> None:
    """Fail fast instead of running with missing, short, placeholder or shared keys."""
    problems = []
    for name, keys in (("DEVICE_KEYS", DEVICE_KEYS), ("VIEWER_KEYS", VIEWER_KEYS)):
        if not keys:
            problems.append(f"{name} is not set")
        for k in keys:
            if len(k) < MIN_KEY_LENGTH or k.lower().startswith(("change-me", "dev-")):
                problems.append(f"{name} has a weak or placeholder key (need {MIN_KEY_LENGTH}+ random characters)")
    if set(DEVICE_KEYS) & set(VIEWER_KEYS):
        problems.append("a device key must not also be a viewer key")
    if problems:
        raise RuntimeError("FallWatch will not start: " + "; ".join(problems)
                           + ". Run: python scripts/make_keys.py")

# --- capture settings handed to the phone at start-up -----------------------
SNAPSHOT_INTERVAL_MS = _int("SNAPSHOT_INTERVAL_MS", 750)   # normal picture interval
BURST_INTERVAL_MS = _int("BURST_INTERVAL_MS", 300)         # while someone is going down
PRE_EVENT_MS = _int("PRE_EVENT_MS", 5_000)                # pictures kept before a fall
POST_EVENT_MS = _int("POST_EVENT_MS", 5_000)              # pictures kept after a fall
HEARTBEAT_SEC = _int("HEARTBEAT_SEC", 0)                   # 0 = only upload on falls
JPEG_QUALITY = float(os.environ.get("JPEG_QUALITY", "0.7"))
FRAME_WIDTH = _int("FRAME_WIDTH", 640)

# --- upload limits ----------------------------------------------------------
MAX_IMAGES_PER_EVENT = _int("MAX_IMAGES_PER_EVENT", 150)
MAX_IMAGE_BYTES = _int("MAX_IMAGE_BYTES", 600_000)
MAX_KEYPOINTS_BYTES = _int("MAX_KEYPOINTS_BYTES", 5_000_000)
