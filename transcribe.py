"""faster-whisper wrapper."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from faster_whisper import WhisperModel


@dataclass
class TranscribeOptions:
    language: str | None = None        # "en", "hi", or None for auto-detect
    task: str = "transcribe"           # "transcribe" or "translate" (-> English)
    initial_prompt: str | None = None  # for dictionary biasing
    beam_size: int = 1
    vad_filter: bool = True


class Transcriber:
    def __init__(
        self,
        model_size: str = "small",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model: WhisperModel | None = None

    def preload(self) -> None:
        """Eagerly load the model. Safe to call from a background thread."""
        self._ensure_loaded()

    def _ensure_loaded(self) -> WhisperModel:
        if self._model is None:
            print(f"[transcribe] loading {self.model_size} on {self.device}/{self.compute_type}...", flush=True)
            self._model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type
            )
        return self._model

    def transcribe(self, audio: np.ndarray, opts: TranscribeOptions | None = None) -> str:
        if audio.size == 0:
            return ""
        opts = opts or TranscribeOptions()
        model = self._ensure_loaded()
        segments, _info = model.transcribe(
            audio,
            language=opts.language,
            task=opts.task,
            initial_prompt=opts.initial_prompt,
            beam_size=opts.beam_size,
            vad_filter=opts.vad_filter,
        )
        text = "".join(seg.text for seg in segments).strip()
        return text
