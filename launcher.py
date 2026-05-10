"""PyInstaller entry point. Runs the openflow package's main() with proper
package context (so absolute and any leftover relative imports both resolve).
"""
import sys

from openflow.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
