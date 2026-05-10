"""CLI entrypoint.

Usage:
  python -m openflow                 # run daemon
  python -m openflow run             # run daemon
  python -m openflow dict list
  python -m openflow dict add NAME --hints "h1,h2" [--lang en|hi|both] [--context TEXT]
  python -m openflow dict remove NAME
  python -m openflow history [--limit 20]
  python -m openflow config path
"""
from __future__ import annotations

import argparse
import sys

from openflow.config import CONFIG_PATH, DICT_PATH, HISTORY_PATH


def _cmd_run(args: argparse.Namespace) -> int:
    from openflow.daemon import main
    main()
    return 0


def _cmd_dict_list(args: argparse.Namespace) -> int:
    from openflow.dictionary import Dictionary
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
    from openflow.dictionary import Dictionary
    d = Dictionary.load()
    hints = [h.strip() for h in (args.hints or "").split(",") if h.strip()]
    d.add(args.name, hints=hints, language=args.lang, context=args.context)
    d.save()
    print(f"added: {args.name}")
    return 0


def _cmd_dict_remove(args: argparse.Namespace) -> int:
    from openflow.dictionary import Dictionary
    d = Dictionary.load()
    ok = d.remove(args.name)
    d.save()
    print("removed" if ok else "not found")
    return 0 if ok else 1


def _cmd_history(args: argparse.Namespace) -> int:
    from openflow.history import History
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

    h = sub.add_parser("history")
    h.add_argument("--limit", type=int, default=20)
    h.set_defaults(func=_cmd_history)

    c = sub.add_parser("config")
    c.set_defaults(func=_cmd_config)

    return ap


def main() -> int:
    ap = build_parser()
    args = ap.parse_args()
    if not getattr(args, "cmd", None):
        return _cmd_run(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
