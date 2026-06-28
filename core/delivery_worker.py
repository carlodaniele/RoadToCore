"""Compatibility entrypoint for the delivery worker."""

from __future__ import annotations

from core.workers.delivery_worker import main, run_once

__all__ = ["main", "run_once"]


if __name__ == "__main__":
    main()
