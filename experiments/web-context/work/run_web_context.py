#!/usr/bin/env python3
"""Use through run.py web. No device or network access occurs on import."""
from web_context.cli import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc))
