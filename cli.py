"""CLI entrypoint.

Usage:
  python -m openflow                 # run daemon
  python -m openflow run             # run daemon
  python -m openflow dict list
  python -m openflow dict add NAME --hints "h1,h2" [--lang en|hi|both] [--context TEXT]
  python -m openflow dict remove NAME
  python -m openflow snippets list
  python -m openflow snippets add "my email" "me@example.com"
  python -m openflow snippets remove "my email"
  python -m openflow history [--limit 20]
  python -m openflow config path
  python -m openflow hub [page]      # open the main window (home, history, …)
"""
from __future__ import annotations

import argparse
import os
import sys

from config import CONFIG_PATH, DICT_PATH, HISTORY_PATH


def _cmd_run(args: argparse.Namespace) -> int:
    from daemon import main
    main()
    return 0


def _cmd_dict_list(args: argparse.Namespace) -> int:
    from dictionary import Dictionary
    d = Dictionary.load()
    if not d.terms:
        print("(empty)")
        return 0
    for t in d.terms:
        hints = ", ".join(t.phonetic_hints) if t.phonetic_hints else "-"
        ctx = f" [{t.context}]" if t.context else ""
        print(f"{t.canonical} ({t.language}){ctx}  hints: {hints}")
    return 0


def _cmd_dict_add(args: argparse.Namespace) -> int:
    from dictionary import Dictionary
    d = Dictionary.load()
    hints = [h.strip() for h in (args.hints or "").split(",") if h.strip()]
    d.add(args.name, hints=hints, language=args.lang, context=args.context)
    d.save()
    print(f"added: {args.name}")
    return 0


def _cmd_dict_remove(args: argparse.Namespace) -> int:
    from dictionary import Dictionary
    d = Dictionary.load()
    ok = d.remove(args.name)
    d.save()
    print("removed" if ok else "not found")
    return 0 if ok else 1


def _cmd_snippets_list(args: argparse.Namespace) -> int:
    from snippets import Snippets
    s = Snippets.load()
    if not s.items:
        print("(empty)")
        return 0
    for item in s.items:
        print(f"{item.trigger!r} -> {item.expansion!r}")
    return 0


def _cmd_snippets_add(args: argparse.Namespace) -> int:
    from snippets import Snippets
    s = Snippets.load()
    # Shells pass "\n" literally; let a signature span lines.
    expansion = args.expansion.replace("\\n", "\n")
    try:
        s.add(args.trigger, expansion)
    except ValueError as e:
        print(f"error: {e}")
        return 1
    s.save()
    print(f"added: {args.trigger}")
    return 0


def _cmd_snippets_remove(args: argparse.Namespace) -> int:
    from snippets import Snippets
    s = Snippets.load()
    ok = s.remove(args.trigger)
    s.save()
    print("removed" if ok else "not found")
    return 0 if ok else 1


def _cmd_dict_edit(args: argparse.Namespace) -> int:
    """Old `dict edit` GUI: now the hub's Dictionary page."""
    return _open_hub("dictionary")


def _cmd_edit_overlay(args: argparse.Namespace) -> int:
    """Launch the edit-mode overlay (subprocess target for daemon)."""
    from ui.edit_overlay import main as overlay_main
    sys.argv = [sys.argv[0], args.selection]
    return overlay_main()


def _cmd_settings(args: argparse.Namespace) -> int:
    """Old settings window: now the hub's Settings page."""
    return _open_hub("settings")


def _cmd_history_viewer(args: argparse.Namespace) -> int:
    """Old history viewer: now the hub's History page."""
    return _open_hub("history")


def _open_hub(page: str) -> int:
    from ui.hub import app as hub_app
    return hub_app.main(page)


def _cmd_hub(args: argparse.Namespace) -> int:
    """Open the main window on a page (single instance: hands off and exits
    if one is already open)."""
    return _open_hub(args.page)


def _cmd_flow_widget(args: argparse.Namespace) -> int:
    """Launch the on-screen flow widget (subprocess target for the daemon)."""
    from ui.flow_widget import main as widget_main
    return widget_main()


def _cmd_onboarding(args: argparse.Namespace) -> int:
    """Launch onboarding wizard."""
    from ui.onboarding import main as on_main
    return on_main()


def _cmd_history(args: argparse.Namespace) -> int:
    from history import History
    h = History()
    rows = h.recent(limit=args.limit)
    for r in rows:
        print(f"[{r.tone}/{r.lang}] {r.final}")
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    print(f"config: {CONFIG_PATH}")
    print(f"dict:   {DICT_PATH}")
    print(f"hist:   {HISTORY_PATH}")
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Diagnostic — reports Accessibility trust, mic, pynput state, key parsing."""
    print("== OpenFlow doctor ==")
    print(f"sys.executable: {sys.executable}")
    print(f"sys.frozen: {getattr(sys, 'frozen', False)}")

    import doctor
    # Accessibility; microphone — try to open
    print(doctor.check_accessibility()["line"])
    print(doctor.check_input_monitoring()["line"])
    print(doctor.check_microphone()["line"])

    # pynput listener — start and watch for 5 seconds
    print("\nListening for any key events for 5s. Press right Option a few times…")
    try:
        from pynput import keyboard
        events: list[str] = []
        def _p(k):
            try:
                events.append(f"press {k}")
            except Exception:
                pass
        def _r(k):
            try:
                events.append(f"release {k}")
            except Exception:
                pass
        li = keyboard.Listener(on_press=_p, on_release=_r)
        li.start()
        import time as _t
        _t.sleep(5.0)
        li.stop()
        print(f"Captured {len(events)} events:")
        for ev in events[:30]:
            print(f"  {ev}")
        if not events:
            print("  (none) — pynput is not receiving key events.")
            print("  Most common cause: this binary lacks Accessibility permission.")
            print("  Check System Settings → Privacy & Security → Accessibility — the")
            print("  entry must point at /Applications/OpenFlow.app/Contents/MacOS/openflow")
            print("  (or simply OpenFlow.app) and be toggled ON.")
    except Exception as e:
        print(f"pynput listener failed: {type(e).__name__}: {e}")

    # Config sanity
    print()
    for c in doctor.check_files():
        print(c["line"])
    print(doctor.check_sarvam_key()["line"])

    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    from openflow_logger import tail_log, _MAIN_LOG, _ERROR_LOG
    level = "errors" if args.errors else "all"
    if args.path:
        print(_ERROR_LOG if args.errors else _MAIN_LOG)
        return 0
    sys.stdout.write(tail_log(level=level, n=args.tail))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="openflow")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("run", help="run the daemon (default)").set_defaults(func=_cmd_run)

    d = sub.add_parser("dict", help="manage custom dictionary")
    dsub = d.add_subparsers(dest="dcmd", required=True)
    dsub.add_parser("list").set_defaults(func=_cmd_dict_list)

    da = dsub.add_parser("add")
    da.add_argument("name")
    da.add_argument("--hints", default="")
    da.add_argument("--lang", default="both", choices=["en", "hi", "both"])
    da.add_argument("--context", default=None)
    da.set_defaults(func=_cmd_dict_add)

    dr = dsub.add_parser("remove")
    dr.add_argument("name")
    dr.set_defaults(func=_cmd_dict_remove)

    dsub.add_parser("edit", help="open the Dictionary page").set_defaults(func=_cmd_dict_edit)

    sn = sub.add_parser("snippets", help="manage snippets (spoken trigger -> text)")
    snsub = sn.add_subparsers(dest="scmd", required=True)
    snsub.add_parser("list").set_defaults(func=_cmd_snippets_list)
    sa = snsub.add_parser("add")
    sa.add_argument("trigger")
    sa.add_argument("expansion", help=r"text to paste; \n for a new line")
    sa.set_defaults(func=_cmd_snippets_add)
    sr = snsub.add_parser("remove")
    sr.add_argument("trigger")
    sr.set_defaults(func=_cmd_snippets_remove)

    eo = sub.add_parser("edit-overlay", help="(internal) launch edit-mode overlay")
    eo.add_argument("selection", help="path to selection text file")
    eo.set_defaults(func=_cmd_edit_overlay)

    sub.add_parser("settings", help="open the Settings page").set_defaults(func=_cmd_settings)
    sub.add_parser("history-viewer", help="open the History page").set_defaults(func=_cmd_history_viewer)
    hb = sub.add_parser("hub", help="open the OpenFlow window")
    hb.add_argument("page", nargs="?", default="home",
                    help="home, insights, history, dictionary, tones, settings or help")
    hb.set_defaults(func=_cmd_hub)
    sub.add_parser("flow-widget", help="(internal) launch the on-screen flow widget").set_defaults(func=_cmd_flow_widget)
    sub.add_parser("onboarding", help="run first-time onboarding wizard").set_defaults(func=_cmd_onboarding)

    h = sub.add_parser("history")
    h.add_argument("--limit", type=int, default=20)
    h.set_defaults(func=_cmd_history)

    c = sub.add_parser("config")
    c.set_defaults(func=_cmd_config)

    sub.add_parser("doctor", help="diagnose permissions + key events").set_defaults(func=_cmd_doctor)

    lg = sub.add_parser("logs", help="show recent log lines")
    lg.add_argument("--tail", type=int, default=50, help="number of trailing lines")
    lg.add_argument("--errors", action="store_true", help="only ERROR-level entries")
    lg.add_argument("--path", action="store_true", help="print log file path instead of contents")
    lg.set_defaults(func=_cmd_logs)

    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()
    if not getattr(args, "cmd", None):
        return _cmd_run(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
