"""SAM 3.1 segmentation adapter for uploaded drawings.

Calls the Meta Model API's Responses endpoint (POST {SEGMENT_BASE_URL}/responses, model sam-3.1) once per
noun phrase, with the image inline, and returns bounding boxes normalised 0 to 1. SAM answers in text, one line
per frame (an image is frame 0), each object as
    <0f>ID<|box;x1=..;y1=..;x2=..;y2=..;w=W;h=H[;c=conf]|><|mask;...|>
with pixel coordinates (x2/y2 inclusive). Only the boxes are used; masks are ignored. Without SEGMENT_MODEL_NAME
set, segment() returns [] and the reader works from the image alone.
"""
import re

import httpx

from .. import config
from .client import ModelError

DEFAULT_PROMPTS = ["ship hull", "tank"]
MAX_BOXES = 40  # per prompt, so the reader's prompt stays small

BOX_RE = re.compile(r"<\|box;([^|]*)\|>")


def _post(image: str, prompt: str) -> str:
    """One noun phrase per request (SAM doesn't mix a phrase with object prompts). Returns the reply text."""
    payload = {"model": config.SEGMENT_MODEL_NAME,
               "input": [{"role": "user", "content": [{"type": "input_image", "image_url": image},
                                                      {"type": "input_text", "text": prompt}]}],
               "metadata": {"include_confidence": "true"}}
    headers = {"Authorization": f"Bearer {config.MODEL_API_KEY}"}
    try:
        r = httpx.post(f"{config.SEGMENT_BASE_URL}/responses", json=payload,
                       headers=headers, timeout=config.MODEL_TIMEOUT_S)
        if r.is_error:
            raise ModelError(f"Segmentation failed: HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise ModelError(f"Segmentation failed: {e}") from e
    parts = [c.get("text", "") for item in data.get("output") or [] if isinstance(item, dict)
             for c in item.get("content") or [] if isinstance(c, dict) and c.get("type") == "output_text"]
    text = "\n".join(parts) or data.get("output_text") or ""
    if not isinstance(text, str):
        raise ModelError("Segmentation failed: no text in the reply")
    return text


def _parse(text: str, label: str) -> list[dict]:
    """Box tokens -> {label, box (pixels, right/bottom exclusive), image size, score}. Fields are read by name."""
    out = []
    for m in BOX_RE.finditer(text):
        f = dict(kv.split("=", 1) for kv in m.group(1).split(";") if "=" in kv)
        try:
            x1, y1, x2, y2 = (float(f[k]) for k in ("x1", "y1", "x2", "y2"))
            w, h = float(f["w"]), float(f["h"])
        except (KeyError, ValueError):
            continue
        score = float(f["c"]) if f.get("c") not in (None, "") else None
        out.append({"label": label, "box": [x1, y1, x2 + 1, y2 + 1], "image_width": w, "image_height": h,
                    "score": score})
    return out[:MAX_BOXES]


def _normalise(seg: dict) -> dict | None:
    """Accept boxes in 0-1 or in pixels (with image width/height in the response)."""
    try:
        x0, y0, x1, y1 = (float(v) for v in seg["box"])
    except (KeyError, TypeError, ValueError):
        return None
    w, h = seg.get("image_width"), seg.get("image_height")
    if max(x0, y0, x1, y1) > 1.0 and w and h:
        x0, x1, y0, y1 = x0 / w, min(x1 / w, 1.0), y0 / h, min(y1 / h, 1.0)
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        return None
    return {"label": str(seg.get("label", "region")), "box": [round(v, 4) for v in (x0, y0, x1, y1)],
            "score": seg.get("score")}


def segment(image: str, prompts: list[str] | None = None) -> list[dict]:
    if not config.segment_configured():
        return []
    out = [_normalise(s) for p in (prompts or DEFAULT_PROMPTS) for s in _parse(_post(image, p), p)]
    return [s for s in out if s]
