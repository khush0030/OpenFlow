"""Run the real PyQt flow widget (ui/flow_widget.py, unchanged) with bench hooks.

Prints `FIRST_FRAME <epoch>` after the first FlowWidget paint and frame-interval
stats on exit, the same lines the Swift prototype prints. Run it with HOME
pointing at a scratch dir so it talks to a scratch socket and logs there,
never to the live ~/.openflow, and with QT_QPA_PLATFORM=offscreen (enforced)
so no window ever reaches the screen.
"""
from __future__ import annotations

import os
import pwd
import sys
import time

# Never show a window: only Qt's offscreen platform is allowed here.
if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
    sys.exit("pyqt_probe: refusing to run without QT_QPA_PLATFORM=offscreen")
_LIVE = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".openflow")
if os.path.realpath(os.path.expanduser("~")) == os.path.realpath(os.path.dirname(_LIVE)):
    sys.exit("pyqt_probe: HOME must be a scratch dir, not the real home")

T_IMPORT0 = time.time()
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

import openflow_logger  # noqa: E402
import ui.flow_widget as fw  # noqa: E402
import widget_channel  # noqa: E402


def _assert_scratch_paths() -> None:
    """Log files and socket must resolve under the scratch HOME; daemon.py is never imported
    (it mirrors print() into the log at import time)."""
    scratch = os.path.realpath(os.path.expanduser("~"))
    live = os.path.realpath(_LIVE)
    for path in (openflow_logger._LOG_DIR, openflow_logger._MAIN_LOG, openflow_logger._ERROR_LOG,
                 widget_channel.SOCKET_PATH):
        real = os.path.realpath(str(path))
        if not real.startswith(scratch + os.sep) or real.startswith(live):
            sys.exit(f"pyqt_probe: {real} is outside the scratch HOME {scratch}")
    if "daemon" in sys.modules:
        sys.exit("pyqt_probe: daemon.py was imported")


_assert_scratch_paths()

_first = False
_stamps: list[float] = []
_draws = 0
_orig_paint = fw.FlowWidget.paintEvent


def _paint(self, e):  # noqa: ANN001
    global _first, _draws
    _orig_paint(self, e)
    _draws += 1
    if not _first:
        _first = True
        print(f"FIRST_FRAME {time.time()}", flush=True)
    if self.view in ("recording", "processing"):
        _stamps.append(time.monotonic())


fw.FlowWidget.paintEvent = _paint

def _report() -> None:
    iv = sorted((b - a) * 1000 for a, b in zip(_stamps, _stamps[1:]) if (b - a) < 0.5)
    if len(iv) < 2:
        print(f"FRAMES draws={_draws} animated=0", flush=True)
        return

    def pct(p: float) -> float:
        return iv[min(len(iv) - 1, int((len(iv) - 1) * p))]

    print(f"FRAMES draws={_draws} animated={len(iv)} mean_ms={sum(iv) / len(iv):.2f} "
          f"p50_ms={pct(.5):.2f} p95_ms={pct(.95):.2f} p99_ms={pct(.99):.2f} max_ms={iv[-1]:.2f}",
          flush=True)


if __name__ == "__main__":
    print(f"IMPORTED {time.time()} import_s={time.time() - T_IMPORT0:.3f}", flush=True)
    code = fw.main()
    _report()
    _assert_scratch_paths()
    sys.exit(code)
