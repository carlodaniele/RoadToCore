"""Compatibility entrypoint for Telegram polling.

Deprecated: use `python -m core.telegram.polling` directly.
"""

from __future__ import annotations

from core.telegram.polling import main, run_once

__all__ = ["main", "run_once"]


if __name__ == "__main__":
    main()
