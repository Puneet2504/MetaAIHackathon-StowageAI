"""Provider-agnostic chat client.

Assumes an endpoint that accepts the widely used chat-completions request shape
(POST {MODEL_BASE_URL}/chat/completions). Check your provider's docs once you have
access to the Meta Model API or an inference partner, and adjust _post() if needed.
"""
import json
import re
from typing import Callable, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .. import config
from . import cache, usage

T = TypeVar("T", bound=BaseModel)


class ModelNotConfigured(RuntimeError):
    pass


class ModelError(RuntimeError):
    pass


def _user_content(user: str, image: str | None):
    """Plain text, or chat-completions content parts when an image (data URL) is attached."""
    if not image:
        return user
    return [{"type": "text", "text": user}, {"type": "image_url", "image_url": {"url": image}}]


def _post(messages: list[dict], temperature: float = 0.0, model: str | None = None,
          json_mode: bool = False, max_tokens: int | None = None, effort: str | None = None,
          cache_reply: bool = True) -> str:
    if not config.model_configured():
        raise ModelNotConfigured(
            "No model configured. Set MODEL_BASE_URL, MODEL_API_KEY and MODEL_NAME in backend/.env")
    model = model or config.MODEL_NAME
    effort = config.REASONING_EFFORT if effort is None else effort
    k = cache.key(model, messages, max_tokens, effort)
    if (hit := cache.get(k)) is not None:
        usage.record(cache_hit=True)
        return hit
    payload = {"model": model, "temperature": temperature, "messages": messages}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if effort:
        payload["reasoning_effort"] = effort
    if json_mode and config.MODEL_JSON_MODE:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {config.MODEL_API_KEY}"}
    try:
        r = httpx.post(f"{config.MODEL_BASE_URL}/chat/completions", json=payload,
                       headers=headers, timeout=config.MODEL_TIMEOUT_S)
        r.raise_for_status()
        body = r.json()
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":  # hit the output cap: never pass off or cache a partial reply
            raise ModelError("Model reply was cut off (finish_reason=length)")
        content = choice["message"]["content"]
        if not content:
            raise ValueError("empty reply (output cap reached?)")
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise ModelError(f"Model call failed: {e}") from e
    u = body.get("usage") or {}
    usage.record(u.get("prompt_tokens", 0), u.get("completion_tokens", 0), call=True)
    if not json_mode and cache_reply:
        cache.put(k, content)  # JSON replies are cached by chat_model once they validate
    return content


def _messages(system: str, user: str, image: str | None = None) -> list[dict]:
    return [{"role": "system", "content": system}, {"role": "user", "content": _user_content(user, image)}]


def _parse(raw: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def chat_text(system: str, user: str, max_tokens: int | None = None,
              validate: Callable[[str], None] | None = None) -> str:
    """Plain-text reply. With `validate` (raises ModelError on a bad reply), only a reply that passes is cached."""
    messages = _messages(system, user)
    if validate is None:
        return _post(messages, temperature=0.2, max_tokens=max_tokens).strip()
    text = _post(messages, temperature=0.2, max_tokens=max_tokens, cache_reply=False).strip()
    validate(text)
    cache.put(cache.key(config.MODEL_NAME, messages, max_tokens, config.REASONING_EFFORT), text)
    return text


def chat_model(system: str, user: str, schema: type[T], image: str | None = None,
               model: str | None = None, retries: int = 1, max_tokens: int | None = None,
               effort: str | None = None) -> T:
    """Ask for JSON and validate it. A reply that fails validation is sent back once for correction."""
    messages = _messages(system, user, image)
    # Cache the validated reply under the original request, so a repeat skips any correction round.
    first_key = cache.key(model or config.MODEL_NAME, messages, max_tokens,
                          config.REASONING_EFFORT if effort is None else effort)
    for attempt in range(retries + 1):
        raw = _post(messages, temperature=0.0, model=model, json_mode=True, max_tokens=max_tokens,
                    effort=effort)
        try:
            out = schema.model_validate(_parse(raw))
            cache.put(first_key, raw)
            return out
        except (ValueError, ValidationError) as e:  # JSONDecodeError is a ValueError
            if attempt == retries:
                raise ModelError(f"Model did not return valid JSON: {e}") from e
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"Your reply was not valid: {e}. Return only the corrected JSON object."},
            ]
    raise AssertionError("unreachable")


def chat_json(system: str, user: str, image: str | None = None, model: str | None = None) -> dict:
    raw = _post(_messages(system, user, image), temperature=0.0, model=model, json_mode=True)
    try:
        return _parse(raw)
    except ValueError as e:
        raise ModelError(f"Model did not return valid JSON: {e}") from e
