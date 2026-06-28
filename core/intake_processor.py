"""Compatibility entrypoint for the intake processing worker."""

from __future__ import annotations

from core.workers.intake_processor import main, run_once

__all__ = ["main", "run_once"]


if __name__ == "__main__":
    main()
