"""Settings read from backend/.env (copied from .env.example by setup.ps1)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")

DATA_DIR = BACKEND_DIR / "data"
VESSEL_DIR = DATA_DIR / "vessels"

MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", "").rstrip("/")
MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_TIMEOUT_S = float(os.getenv("MODEL_TIMEOUT_S", "60"))

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")


def model_configured() -> bool:
    return bool(MODEL_BASE_URL and MODEL_API_KEY and MODEL_NAME)
