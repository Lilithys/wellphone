#!/usr/bin/env python3
"""Delivery convenience entry: delegate unchanged to the verified core only."""
import os
import sys
from pathlib import Path

if __name__ == "__main__":
    entry = Path(__file__).resolve().parent / "core" / "run.py"
    os.execv(sys.executable, [sys.executable, str(entry), *sys.argv[1:]])
