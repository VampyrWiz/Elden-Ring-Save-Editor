#!/usr/bin/env python3
"""Thin launcher (kept for backward compatibility). Full app is src/er_save_editor/."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from er_save_editor.cli import main

if __name__ == "__main__":
    sys.exit(main())
