from __future__ import annotations

import sys

from core.config.env_validation import collect_missing_env_keys


def main() -> int:
    missing = collect_missing_env_keys()

    if not missing:
        print("OK: all required environment variables are present.")
        return 0

    print("ERROR: missing required environment variables:")
    for key in missing:
        print(f"- {key}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
