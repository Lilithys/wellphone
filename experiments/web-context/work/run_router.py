#!/usr/bin/env python3
"""Use via the worktree root run.py; no phone action occurs on import."""
from router.cli import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc))
