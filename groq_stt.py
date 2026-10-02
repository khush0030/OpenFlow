"""Groq Whisper: the second cloud speech-to-text, used only when Sarvam's
stream and upload both fail or are too slow (transcribe.Transcriber).

OpenAI-compatible audio endpoints. The key is the one the Groq cleanup
provider already uses (llm.FAST_PROVIDERS["groq"]): env OPENFLOW_GROQ_API_KEY
or Keychain service "openflow", account "groq_api_key". A shell-wide
GROQ_API_KEY is never picked up, so audio only goes to Groq when the user
put a key there for OpenFlow. [failover] stt = "off" turns it off.

Language modes (Saaras mode -> Whisper), gaps noted in the spec
(docs/superpowers/specs/2026-10-02-provider-failover.md):
    translate                 -> /audio/translations (English out), whisper-large-v3
    transcribe / verbatim     -> /audio/transcriptions, language from the code
    translit (Roman Hindi)    -> Hindi transcription: Devanagari, not Roman
    codemix (Hinglish)        -> auto-detect: one script, not mixed
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

from sarvam import STTResult, _http, find_api_key

TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
TRANSLATE_URL = "https://api.groq.com/openai/v1/audio/translations"
KEYRING_USER = "groq_api_key"
DEFAULT_ENV = "OPENFLOW_GROQ_API_KEY"
# Groq's turbo model doesn't translate; large-v3 does.
DEFAULT_MODEL = "whisper-large-v3-turbo"
DEFAULT_TRANSLATE_MODEL = "whisper-large-v3"
# Whisper's prompt is ~224 tokens; names only need a short list.
PROMPT_MAX_CHARS = 400
# A segment Whisper itself thinks is probably not speech ("Thank you." on
# room noise) is dropped.
NO_SPEECH_DROP = 0.8

_LANG = {"english": "en-IN", "en": "en-IN", "hindi": "hi-IN", "hi": "hi-IN"}


class GroqSTTError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _iso639(language_code: str | None) -> str | None:
    if not language_code or language_code == "unknown":
        return None
    return language_code.split("-")[0].lower() or None


def _prompt(keyterms) -> str | None:
    terms = [t for t in (keyterms or ()) if t]
    if not terms:
        return None
    out = ""
    for t in terms:
        nxt = f"{out}, {t}" if out else t
        if len(nxt) > PROMPT_MAX_CHARS:
            break
        out = nxt
    return out + "." if out else None


class GroqWhisper:
    name = "groq"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
                 translate_model: str = DEFAULT_TRANSLATE_MODEL,
                 timeout: float = 20.0) -> None:
        self._api_key = api_key
        self.model = model
        self.translate_model = translate_model
        self.timeout = timeout

    def request(self, mode: str, language_code: str | None,
                keyterms=()) -> tuple[str, dict[str, str]]:
        """(url, form fields) for a Saaras mode + language. Pure."""
        data = {"response_format": "verbose_json", "temperature": "0"}
        if mode == "translate":
            data["model"] = self.translate_model
            url = TRANSLATE_URL
        else:
            data["model"] = self.model
            url = TRANSCRIBE_URL
            lang = _iso639(language_code)
            if lang:
                data["language"] = lang
        prompt = _prompt(keyterms)
        if prompt:
            data["prompt"] = prompt
        return url, data

    def transcribe(self, wav: bytes, *, mode: str = "transcribe",
                   language_code: str | None = None, keyterms=()) -> STTResult:
        url, data = self.request(mode, language_code, keyterms)
        try:
            resp = _http().post(
                url, headers={"Authorization": f"Bearer {self._api_key}"},
                data=data, files={"file": ("clip.wav", wav, "audio/wav")},
                timeout=self.timeout)
        except Exception as e:
            raise GroqSTTError(f"groq request failed: {e}") from e
        if resp.status_code >= 400:
            raise GroqSTTError(f"groq {resp.status_code}: {resp.text[:300]}",
                               resp.status_code)
        try:
            body = resp.json()
        except Exception as e:
            raise GroqSTTError(f"groq returned non-JSON: {resp.text[:200]}") from e
        return parse(body)


def parse(body: Any) -> STTResult:
    if not isinstance(body, dict):
        raise GroqSTTError(f"unexpected groq response: {body!r}"[:300])
    segments = body.get("segments")
    if isinstance(segments, list) and segments:
        kept = [str(s.get("text") or "").strip() for s in segments
                if isinstance(s, dict)
                and float(s.get("no_speech_prob") or 0.0) < NO_SPEECH_DROP]
        text = " ".join(t for t in kept if t)
    else:
        text = str(body.get("text") or "")
    lang = str(body.get("language") or "").strip().lower()
    return STTResult(transcript=" ".join(text.split()), language_code=_LANG.get(lang))


def _settings(cfg: dict | None) -> tuple[dict, dict]:
    from config import DEFAULTS
    cfg = cfg or {}
    f = {**DEFAULTS["failover"], **(cfg.get("failover") or {})}
    c = {**DEFAULTS["cleanup"], **(cfg.get("cleanup") or {})}
    return f, c


def enabled(cfg: dict | None) -> bool:
    f, _ = _settings(cfg)
    return str(f.get("stt", "auto")).strip().lower() not in ("off", "false", "no", "0")


def find_key(cfg: dict | None, *, find=find_api_key) -> str | None:
    _, c = _settings(cfg)
    return find(c.get("groq_api_key_env") or DEFAULT_ENV, KEYRING_USER)


def from_config(cfg: dict | None, *,
                find: Callable[[str, str], str | None] = find_api_key) -> GroqWhisper | None:
    """The fallback STT [failover] allows, or None (off, or no Groq key)."""
    if not enabled(cfg):
        return None
    key = find_key(cfg, find=find)
    if not key:
        return None
    f, _ = _settings(cfg)
    return GroqWhisper(key, model=str(f["groq_stt_model"]),
                       translate_model=str(f["groq_translate_model"]))


def has_speech(audio: np.ndarray, threshold: float | None, sample_rate: int) -> bool:
    """Any 20 ms frame at or above `threshold`: Whisper invents text for
    silence, so a clip without one is not sent."""
    if not threshold or threshold <= 0 or audio.size == 0:
        return audio.size > 0
    frame = max(1, int(sample_rate * 0.02))
    n = audio.size // frame
    if n == 0:
        return False
    frames = np.asarray(audio[: n * frame], dtype=np.float32).reshape(n, frame)
    return bool((np.sqrt(np.mean(frames * frames, axis=1)) >= threshold).any())
