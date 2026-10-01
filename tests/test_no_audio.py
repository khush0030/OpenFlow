"""'Can't hear you' detection: audio.loudest_rms / heard_nothing and the
flow controller's no-audio error (ROADMAP Phase 2)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np

import config as cfg_mod
from audio import NO_INPUT_RMS, heard_nothing, loudest_rms
from flow_state import ERROR, IDLE, NO_AUDIO, RETRY_WINDOW_S, FlowController, FlowHooks

SR = 16000


def test_loudest_rms_of_silence_is_zero():
    assert loudest_rms(np.zeros(SR, dtype=np.float32), SR) == 0.0
    assert loudest_rms(np.zeros(0, dtype=np.float32), SR) == 0.0


def test_loudest_rms_finds_a_short_burst_in_a_long_take():
    x = np.zeros(SR * 30, dtype=np.float32)
    x[SR * 10: SR * 10 + 800] = 0.1           # 50 ms at 0.1
    assert abs(loudest_rms(x, SR) - 0.1) < 1e-6


def test_loudest_rms_of_a_take_shorter_than_one_window():
    assert abs(loudest_rms(np.full(100, 0.2, dtype=np.float32), SR) - 0.2) < 1e-6


def test_heard_nothing_threshold():
    assert heard_nothing(np.zeros(SR, dtype=np.float32), SR)
    assert heard_nothing(np.full(SR, NO_INPUT_RMS / 2, dtype=np.float32), SR)
    assert not heard_nothing(np.full(SR, 0.02, dtype=np.float32), SR)


def test_default_threshold_in_config():
    assert cfg_mod.DEFAULTS["audio"]["no_input_rms"] == NO_INPUT_RMS


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_flow():
    sent = []
    hooks = FlowHooks(start_recording=lambda: None, finish_recording=lambda: None,
                      cancel_recording=lambda: None, rerun=lambda audio, target, run: None,
                      copy_text=lambda t: None, save_setting=lambda k, v: None)
    clock = Clock()
    return FlowController(sent.append, hooks, clock=clock), sent, clock


def test_no_audio_is_an_error_with_a_reason():
    flow, sent, _ = make_flow()
    flow.recording_started()
    flow.no_audio(np.zeros(SR), None)
    assert flow.state == ERROR and flow.reason == NO_AUDIO
    assert sent[-1] == {"type": "state", "state": ERROR, "text": "", "reason": NO_AUDIO}


def test_no_audio_error_times_out_like_any_error():
    flow, sent, clock = make_flow()
    flow.no_audio(np.zeros(SR), None)
    clock.t = RETRY_WINDOW_S - 1
    flow.tick()
    assert flow.state == ERROR
    clock.t = RETRY_WINDOW_S + 1
    flow.tick()
    assert flow.state == IDLE and flow.reason == ""
    assert "reason" not in sent[-1]


def test_reason_clears_on_the_next_state():
    flow, sent, _ = make_flow()
    flow.no_audio(np.zeros(SR), None)
    flow.recording_started()
    assert flow.reason == "" and "reason" not in sent[-1]
    run = flow.processing()
    flow.failed(np.zeros(SR), None, run=run)       # a real failure: Retry, no reason
    assert flow.state == ERROR and "reason" not in sent[-1]
