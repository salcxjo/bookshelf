"""A thin client for Google's Gemini API, which has a free tier (key from Google AI Studio).

Configuration, normally in .env (see .env.example):
    GEMINI_API_KEY   required; without it the AI features switch themselves off
    GEMINI_MODEL     optional; model names change often, so check AI Studio for a current
                     Flash model if the default stops working

Only titles, authors, star ratings and public synopses are ever sent: never notes or highlights.
Keep this module small: if you later want a local model (e.g. Ollama), only generate_json changes.
"""
import json
import os
import re

import requests

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-flash-latest"   # an alias for the newest Flash model, if Google still offers it
TIMEOUT = 45  # seconds


class LLMNotConfigured(Exception):
    """No API key is set."""


class LLMRateLimited(Exception):
    """The free-tier quota is used up for now."""


class LLMError(Exception):
    """Anything else that went wrong talking to the model."""


def api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY", "").strip() or None


def model_name() -> str:
    return os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL


def is_configured() -> bool:
    return api_key() is not None


def _error_message(response: requests.Response) -> str:
    try:
        return str(response.json()["error"]["message"])[:300]
    except (ValueError, KeyError, TypeError):
        return (response.text or response.reason or "")[:300]


def _text_of(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        reason = (payload.get("promptFeedback") or {}).get("blockReason")
        raise LLMError(f"the model returned no answer{f' (blocked: {reason})' if reason else ''}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _parse_json(text: str):
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())   # tolerate markdown fences
    try:
        return json.loads(text)
    except ValueError as exc:
        raise LLMError("the model didn't return valid JSON") from exc


def generate_json(prompt: str):
    """Send one prompt and return the model's reply parsed as JSON.

    Raises LLMNotConfigured, LLMRateLimited or LLMError. The key travels in a header,
    never in the URL, so it can't leak into logs or error messages.
    """
    key = api_key()
    if key is None:
        raise LLMNotConfigured("set GEMINI_API_KEY to enable the AI features")

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
    }
    try:
        response = requests.post(f"{API_ROOT}/{model_name()}:generateContent", json=body,
                                 headers={"x-goog-api-key": key}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise LLMError(f"couldn't reach the Gemini API ({type(exc).__name__})") from exc

    if response.status_code == 429:
        raise LLMRateLimited("The free Gemini quota is used up for now. Try again in a minute, or tomorrow.")
    if response.status_code >= 400:
        message = _error_message(response)
        if response.status_code == 404:
            message += f" (model {model_name()!r}; set GEMINI_MODEL to a current model name)"
        raise LLMError(f"Gemini API error {response.status_code}: {message}")
    try:
        return _parse_json(_text_of(response.json()))
    except ValueError as exc:
        raise LLMError("the Gemini API sent an unreadable response") from exc
