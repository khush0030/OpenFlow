"""PyInstaller entry point.

Calls multiprocessing.freeze_support() *first* so PyInstaller's frozen
binary can spawn its own helper subprocesses (resource_tracker, semaphore
manager) without hitting our argparse, which would reject the helpers'
exec args and cause the parent daemon to die after `[daemon] ready`.
"""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from cli import main
    sys.exit(main())
