"""Tap / double-tap timing for the hold key, shared by both hotkey backends
(hotkeys_nsevent.py, hotkeys.py) so they can't drift apart."""

# A press+release shorter than this is a tap, not a hold. Real taps in the
# log (2026-10-01) ran 46-340 ms, several at 313-340 ms, right on the old
# 350 ms limit; 450 leaves room without catching short holds.
SHORT_TAP_MS = 450

# The second press of a double-tap must come within this long of the first
# tap's release. Was 600: 98% of real double-tap gaps are under 300 ms
# (median 80, p90 119), and 5 of the 8 presses at 300-600 ms were accidental
# hands-free starts (audit 2026-10-11). It is also how long a lone tap's
# pill and mic linger before closing.
DOUBLE_TAP_GAP_MS = 350
