"""`python -m openflow` entrypoint (repo is a flat module layout, not a package)."""
from cli import main

if __name__ == "__main__":
    raise SystemExit(main())
