"""Settings read from backend/.env (copied from .env.example by setup.ps1)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")

DATA_DIR = BACKEND_DIR / "data"
VESSEL_DIR = DATA_DIR / "vessels"
USER_DIR = Path(os.getenv("USER_DATA_DIR", DATA_DIR / "users"))
CACHE_DIR = DATA_DIR / "cache"

MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", "").rstrip("/")
MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_TIMEOUT_S = float(os.getenv("MODEL_TIMEOUT_S", "60"))
# Ask for response_format json_object; turn on only if the provider supports it.
MODEL_JSON_MODE = os.getenv("MODEL_JSON_MODE", "false").lower() in ("1", "true", "yes")
# Output caps per task, in tokens. Reasoning models count their hidden reasoning here too,
# so these are safety limits, not targets.
MAX_TOKENS_EXPLAIN = int(os.getenv("MAX_TOKENS_EXPLAIN", "2000"))  # room for hidden reasoning plus the reply
MAX_TOKENS_OPEN_SPACE = int(os.getenv("MAX_TOKENS_OPEN_SPACE", "8000"))  # the AI checks tanks, lines and cleaning itself
REASONING_EFFORT_OPEN_SPACE = os.getenv("REASONING_EFFORT_OPEN_SPACE", "low")
MAX_TOKENS_CHECKLIST = int(os.getenv("MAX_TOKENS_CHECKLIST", "1800"))  # cleaning checklists cover several ports
MAX_TOKENS_JSON = int(os.getenv("MAX_TOKENS_JSON", "1200"))
MAX_TOKENS_DRAWING = int(os.getenv("MAX_TOKENS_DRAWING", "8000"))  # big GA drawings: 30+ tanks
# How much hidden reasoning to ask for (OpenAI-style reasoning_effort). Empty = don't send it.
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "minimal")
REASONING_EFFORT_DRAWING = os.getenv("REASONING_EFFORT_DRAWING", "low")
# When a drawing gives only LOA, LBP is estimated as this share of it (typically 0.93-0.95) and flagged.
LBP_FROM_LOA = float(os.getenv("LBP_FROM_LOA", "0.94"))

# Reads drawings (text + image). Muse Spark or Muse Wonder; falls back to MODEL_NAME.
READER_MODEL_NAME = os.getenv("READER_MODEL_NAME", "") or MODEL_NAME
# SAM 3.1 segmentation of uploaded drawings. Optional: without it the reader sees only the image.
SEGMENT_BASE_URL = os.getenv("SEGMENT_BASE_URL", "").rstrip("/") or MODEL_BASE_URL
SEGMENT_MODEL_NAME = os.getenv("SEGMENT_MODEL_NAME", "")
# Muse Image, reserved for vessel sketches (not used yet).
IMAGE_MODEL_NAME = os.getenv("IMAGE_MODEL_NAME", "")

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

# Signs login tokens. Empty = one is generated once into data/users/.secret.
SECRET_KEY = os.getenv("SECRET_KEY", "")
TOKEN_DAYS = float(os.getenv("TOKEN_DAYS", "7"))
# Demo account created at start-up so judges can sign in without registering.
# Demo-only credential protecting synthetic data; change it in .env for anything else.
DEMO_USER_EMAIL = os.getenv("DEMO_USER_EMAIL", "demo@stowage-ai.example")
DEMO_USER_PASSWORD = os.getenv("DEMO_USER_PASSWORD", "DemoFjord-2026")


def model_configured() -> bool:
    return bool(MODEL_BASE_URL and MODEL_API_KEY and MODEL_NAME)


def segment_configured() -> bool:
    return bool(SEGMENT_BASE_URL and MODEL_API_KEY and SEGMENT_MODEL_NAME)
