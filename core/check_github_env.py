from __future__ import annotations

import sys

from core.config.env_validation import collect_missing_env_keys


def _parse_role(argv: list[str]) -> str | None:
    if len(argv) <= 1:
        return None
    return argv[1].strip() or None


def main() -> int:
    runtime_role = _parse_role(sys.argv)
    missing = collect_missing_env_keys(runtime_role=runtime_role)

    if not missing:
        print("OK: all required environment variables are present.")
        return 0

    print("ERROR: missing required environment variables:")
    for key in missing:
        print(f"- {key}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
