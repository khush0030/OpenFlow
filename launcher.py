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
    # freeze_support() only intercepts --multiprocessing-fork; the
    # resource_tracker helper is spawned as `<exe> -c "from multiprocessing..."`.
    if len(sys.argv) >= 3 and sys.argv[1] == "-c" and "multiprocessing" in sys.argv[2]:
        exec(sys.argv[2])
        sys.exit(0)
    from cli import main
    sys.exit(main())
