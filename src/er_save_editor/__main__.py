"""Allows `python3 src/er_save_editor [args]` from anywhere."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from er_save_editor.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
