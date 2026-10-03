# Widget placement: active screen, notch, Dock (2026-10-02)

Phase 5 ("Widget 2.0"). Extends §3 of `2026-09-30-flow-widget-design.md`.
Resting size and the three positions (left edge, bottom centre, right edge)
do not change; this is only about *which* screen and *which part* of it.

## Today

`FlowApp._screen_rect` uses the display under the **mouse**, re-checked every
250 ms and on every state change. So the widget jumps displays mid-dictation
when the mouse moves, and the "frontmost window" rule in the widget spec was
never implemented. It uses Qt's `availableGeometry` (menu bar and Dock already
excluded) but knows nothing about the notch.

## Which screen

**The screen holding the focused window** (the frontmost app's front window,
by largest overlap), falling back to the screen under the mouse when that app
has no window (Finder desktop, a menu-bar app), then to the current screen,
then the primary.

Why the focused window and not the mouse: the widget reports on the text you
are dictating, which lands in the focused window. On a laptop + external
monitor the mouse is often parked on the other display while you type (or
read). The 2026-09-30 spec already said "frontmost window". Finding it costs
one `CGWindowListCopyWindowInfo` call (~1.5 ms; bounds need no Screen
Recording permission).

**When it may move** (no annoying motion):

| Moment | Re-pick the screen? |
|---|---|
| Idle (resting bar) | yes, checked every 250 ms; jumps without animation |
| Hover | no (it's under your mouse) |
| A session starts (anything → recording, except mid-session) | yes, once |
| Recording / processing / card / toasts | no: stays where the session started |
| Its screen is unplugged or changes size / Dock / resolution | yes, immediately (Qt screen signals + the 250 ms check) |

A move to another display is never animated (no flying across the desk).

## Which part of the screen

The widget and its pop-ups live in the **usable area**:

- Qt `availableGeometry` (= `NSScreen.visibleFrame`): excludes the menu bar
  and the Dock wherever it is (left, bottom, right; a hidden Dock leaves its
  4 pt reveal strip, which we also avoid).
- minus the notch band: top inset = `NSScreen.safeAreaInsets.top`, and below
  the notch rect (the gap between `auxiliaryTopLeftArea` and
  `auxiliaryTopRightArea`). This matters when the menu bar is hidden
  (full-screen apps, "Automatically hide the menu bar"), where `visibleFrame`
  can reach the top of a notched screen.

Left/right positions are vertically centred in that area; bottom centre sits
10 pt above its bottom. Pop-ups are clamped to the same area, so nothing
touches the notch, the menu bar or the Dock.

**Full-screen spaces:** the widget already joins them
(`fullScreenAuxiliary`); with the menu bar hidden the safe-area clamp keeps it
off the notch. **Stage Manager:** its strip isn't reported by macOS, so it is
not avoided; the left-edge bar is 7 pt wide and sits on top of it. Accepted.

## Code

- `ui/widget_geometry.py` (pure, unit tested): `Display`, `usable_area`,
  `notch_rect`, `ns_to_qt`, `display_at`, `display_for_window`,
  `pick_display`, `may_change_display`, `place`.
- `ui/screens.py` (new, AppKit/Quartz/Qt glue): reads displays (Qt +
  NSScreen safe areas), the focused window, the mouse; `ScreenTracker`
  applies the policy and calls back on screen add/remove/geometry change.
- `ui/flow_widget.py`: a small hook; `_screen_rect` asks the tracker, the
  tracker's change signal re-runs the existing follow check, and a move
  between displays is not animated.

## On-screen checks

Laptop only: idle bar right/left/bottom clears the Dock (try Dock left,
bottom, right, auto-hide). With an external display: focus a window on each
display; the idle bar follows within ¼ s. Start dictating on one, move the
mouse/focus to the other: it stays until the session ends. Unplug the
display mid-session: it lands on the laptop. Notched MacBook: hide the menu
bar or go full screen; the widget never touches the notch.
