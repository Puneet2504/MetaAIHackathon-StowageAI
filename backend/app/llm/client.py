"""Provider-agnostic chat client.

Assumes an endpoint that accepts the widely used chat-completions request shape
(POST {MODEL_BASE_URL}/chat/completions). Check your provider's docs once you have
access to the Meta Model API or an inference partner, and adjust _post() if needed.
"""
import json
import re

import httpx

from .. import config


class ModelNotConfigured(RuntimeError):
    pass


class ModelError(RuntimeError):
    pass


def _post(system: str, user: str, temperature: float = 0.0) -> str:
    if not config.model_configured():
        raise ModelNotConfigured(
            "No model configured. Set MODEL_BASE_URL, MODEL_API_KEY and MODEL_NAME in backend/.env")
    payload = {
        "model": config.MODEL_NAME,
        "temperature": temperature,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    headers = {"Authorization": f"Bearer {config.MODEL_API_KEY}"}
    try:
        r = httpx.post(f"{config.MODEL_BASE_URL}/chat/completions", json=payload,
                       headers=headers, timeout=config.MODEL_TIMEOUT_S)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise ModelError(f"Model call failed: {e}") from e


def chat_text(system: str, user: str) -> str:
    return _post(system, user, temperature=0.2).strip()


def chat_json(system: str, user: str) -> dict:
    raw = _post(system, user, temperature=0.0)
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ModelError(f"Model did not return valid JSON: {e}") from e
