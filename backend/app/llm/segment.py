"""SAM 3.1 segmentation adapter for uploaded drawings.

Sends the image with text prompts and returns bounding boxes, normalised 0 to 1.
The request and response shape below is an assumption; check the Meta Model API
docs for SAM 3.1 and adjust _post() if it differs. Without SEGMENT_MODEL_NAME set,
segment() returns [] and the reader works from the image alone.
"""
import httpx

from .. import config
from .client import ModelError

DEFAULT_PROMPTS = ["ship hull", "tank"]


def _post(image: str, prompts: list[str]) -> list[dict]:
    payload = {"model": config.SEGMENT_MODEL_NAME, "image": image, "prompts": prompts,
               "output": "boxes"}
    headers = {"Authorization": f"Bearer {config.MODEL_API_KEY}"}
    try:
        r = httpx.post(f"{config.SEGMENT_BASE_URL}/segment", json=payload,
                       headers=headers, timeout=config.MODEL_TIMEOUT_S)
        r.raise_for_status()
        return r.json()["segments"]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise ModelError(f"Segmentation failed: {e}") from e


def _normalise(seg: dict) -> dict | None:
    """Accept boxes in 0-1 or in pixels (with image width/height in the response)."""
    try:
        x0, y0, x1, y1 = (float(v) for v in seg["box"])
    except (KeyError, TypeError, ValueError):
        return None
    w, h = seg.get("image_width"), seg.get("image_height")
    if max(x0, y0, x1, y1) > 1.0 and w and h:
        x0, x1, y0, y1 = x0 / w, x1 / w, y0 / h, y1 / h
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        return None
    return {"label": str(seg.get("label", "region")), "box": [round(v, 4) for v in (x0, y0, x1, y1)],
            "score": seg.get("score")}


def segment(image: str, prompts: list[str] | None = None) -> list[dict]:
    if not config.segment_configured():
        return []
    out = [_normalise(s) for s in _post(image, prompts or DEFAULT_PROMPTS)]
    return [s for s in out if s]
