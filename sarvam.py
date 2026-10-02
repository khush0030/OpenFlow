"""Sarvam AI HTTP client: speech-to-text + chat completions.

Auth is a single subscription key (`SARVAM_API_KEY` / Keychain). Both the
transcriber and the cleanup LLM go through this module so the pipeline is
one vendor end-to-end.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

from config import load_env

STT_URL = "https://api.sarvam.ai/speech-to-text"
CHAT_URL = "https://api.sarvam.ai/v1/chat/completions"
KEYRING_SERVICE = "openflow"
KEYRING_USER = "sarvam_api_key"
DEFAULT_API_KEY_ENV = "SARVAM_API_KEY"

# Sync STT rejects clips longer than 30s; leave a little headroom.
STT_MAX_SECONDS = 28.0


class SarvamError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class STTResult:
    transcript: str
    language_code: str | None = None
    language_probability: float | None = None
    request_id: str | None = None


def find_api_key(
    api_key_env: str = DEFAULT_API_KEY_ENV,
    keyring_user: str = KEYRING_USER,
) -> str | None:
    """Env → Keychain (service "openflow", account `keyring_user`) →
    ~/.openflow/.env / repo .env. None if the key is nowhere."""
    load_env()
    key = os.environ.get(api_key_env, "").strip()
    if key:
        return key
    try:
        import keyring  # type: ignore
        key = (keyring.get_password(KEYRING_SERVICE, keyring_user) or "").strip()
        if key:
            os.environ[api_key_env] = key
            return key
    except Exception as e:
        print(f"[sarvam] keyring read failed: {e}", flush=True)
    for env_path in (
        os.path.expanduser("~/.openflow/.env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    ):
        if not os.path.exists(env_path):
            continue
        try:
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    if k.strip() == api_key_env:
                        key = v.strip().strip('"').strip("'")
                        if key:
                            os.environ[api_key_env] = key
                            return key
        except Exception:
            pass
    return None


def resolve_api_key(api_key_env: str = DEFAULT_API_KEY_ENV) -> str:
    """Env → Keychain → ~/.openflow/.env / repo .env. Raises if missing."""
    key = find_api_key(api_key_env)
    if key:
        return key
    raise SarvamError(
        f"Missing Sarvam API key. Set ${api_key_env}, run onboarding, "
        "or place it in ~/.openflow/.env"
    )


def _headers(api_key: str) -> dict[str, str]:
    return {"api-subscription-key": api_key}


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception:
        return resp.text[:400] or f"HTTP {resp.status_code}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err)
    if isinstance(err, str):
        return err
    return str(body)[:400]


_client: httpx.Client | None = None

# How long an idle pooled connection is kept. httpx's default (5s) dropped
# it between almost every pair of dictations, so each one paid a fresh
# TCP + TLS handshake. A connection the server has closed in the meantime
# is detected and replaced before reuse.
KEEPALIVE_S = 120.0


def _http() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(
            timeout=25.0,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20,
                                keepalive_expiry=KEEPALIVE_S),
        )
    return _client


# Warm-up: a bare HEAD to a host opens (or refreshes) the pooled connection,
# so the dictation's real request skips the handshake. Sent at key-down; the
# user is still talking while it runs. No key, no payload, no quota.
WARM_EVERY_S = 10.0
_warm_lock = threading.Lock()
_warmed_at: dict[str, float] = {}


def warm(url: str | None, *, clock=time.monotonic) -> bool:
    """Open a connection to `url`'s host in the background, at most once per
    WARM_EVERY_S per host. Returns True if a warm-up was started."""
    if not url:
        return False
    origin = httpx.URL(url).copy_with(path="/", query=None)
    host = str(origin)
    now = clock()
    with _warm_lock:
        if now - _warmed_at.get(host, float("-inf")) < WARM_EVERY_S:
            return False
        _warmed_at[host] = now

    def run() -> None:
        try:
            _http().head(host, timeout=5.0)
        except Exception:
            pass       # best effort: the real request connects as before
    threading.Thread(target=run, name="http-warm", daemon=True).start()
    return True


def _request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    retries: int = 2,
    **kwargs: Any,
) -> httpx.Response:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = _http().request(
                method, url, headers=headers, timeout=timeout, **kwargs
            )
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                last_err = SarvamError(_error_message(resp), resp.status_code)
                time.sleep(0.4 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise SarvamError(
                    f"Sarvam {resp.status_code}: {_error_message(resp)}",
                    resp.status_code,
                )
            return resp
        except httpx.TimeoutException as e:
            last_err = e
            time.sleep(0.4 * (attempt + 1))
        except httpx.TransportError as e:
            last_err = e
            time.sleep(0.4 * (attempt + 1))
    raise SarvamError(f"Sarvam request failed after retries: {last_err}")


def speech_to_text(
    wav_bytes: bytes,
    *,
    api_key: str,
    model: str = "saaras:v4",
    mode: str = "transcribe",
    language_code: str | None = None,
    timeout: float = 25.0,
    keyterms: list[str] | None = None,
) -> STTResult:
    data: dict[str, str] = {
        "model": model,
        "mode": mode,
        "language_code": language_code or "unknown",
    }
    # Names/terms to bias recognition toward (screen_context.py). Saaras v4
    # only: up to 50 terms, one JSON-encoded array in one form field.
    if keyterms and model.startswith("saaras:v4"):
        data["keyterms"] = json.dumps(list(keyterms)[:50], ensure_ascii=False)
    files = {"file": ("clip.wav", wav_bytes, "audio/wav")}
    resp = _request_with_retry(
        "POST",
        STT_URL,
        headers=_headers(api_key),
        timeout=timeout,
        data=data,
        files=files,
    )
    body = resp.json()
    transcript = (body.get("transcript") or "").strip()
    prob = body.get("language_probability")
    try:
        prob_f = float(prob) if prob is not None else None
    except (TypeError, ValueError):
        prob_f = None
    return STTResult(
        transcript=transcript,
        language_code=body.get("language_code"),
        language_probability=prob_f,
        request_id=body.get("request_id"),
    )


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type", "text") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts).strip()
    if isinstance(content, str) and content.strip():
        return content.strip()
    return ""


def chat_complete(
    messages: list[dict[str, str]],
    *,
    api_key: str,
    model: str = "sarvam-105b",
    max_tokens: int = 1024,
    temperature: float = 0.2,
    timeout: float = 45.0,
    reasoning_effort: str | None = None,
) -> str:
    # sarvam-105b thinks by default; reasoning tokens count toward max_tokens
    # and can leave `content` empty (finish_reason=length). Dictation cleanup
    # wants the reply only.
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "reasoning_effort": reasoning_effort,
    }
    resp = _request_with_retry(
        "POST",
        CHAT_URL,
        headers={**_headers(api_key), "Content-Type": "application/json"},
        timeout=timeout,
        json=payload,
    )
    body = resp.json()
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise SarvamError(f"Unexpected chat response: {body!r}") from e
    text = _message_text(message)
    if not text:
        raise SarvamError(
            "Sarvam returned empty content "
            f"(finish_reason={body['choices'][0].get('finish_reason')!r})"
        )
    return text
