"""Short-lived worker that delivers pending outbox payloads to enabled adapters."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from core.config.env_validation import RUNTIME_ROLE_DELIVERY_WORKER, validate_env_for_runtime
from core.delivery.dispatch import AstroConfig, DeliveryConfig, DeliveryDispatcher, WordPressConfig
from core.delivery.outbox_worker import deliver_pending_outbox


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _build_delivery_dispatcher(outbox_dir: Path) -> DeliveryDispatcher:
    config = DeliveryConfig(
        retries=int(os.getenv("DELIVERY_RETRIES", "2")),
        retry_backoff_seconds=float(os.getenv("DELIVERY_RETRY_BACKOFF_SECONDS", "2.0")),
        wp=WordPressConfig(
            enabled=_is_true(os.getenv("DELIVERY_WORDPRESS_ENABLED", "false")),
            endpoint=os.getenv("DELIVERY_WORDPRESS_ENDPOINT", "").strip(),
            username=os.getenv("DELIVERY_WORDPRESS_USERNAME", "").strip(),
            app_password=os.getenv("DELIVERY_WORDPRESS_APP_PASSWORD", "").strip(),
            timeout=float(os.getenv("DELIVERY_WORDPRESS_TIMEOUT", "30")),
            assets_public_url=os.getenv("ROADTOCORE_ASSETS_PUBLIC_URL", "").strip(),
        ),
        astro=AstroConfig(
            enabled=_is_true(os.getenv("DELIVERY_ASTRO_ENABLED", "false")),
            node_bin=os.getenv("DELIVERY_ASTRO_NODE_BIN", "node").strip(),
            adapter_dist_path=os.getenv("DELIVERY_ASTRO_ADAPTER_DIST", "").strip(),
            content_dir=os.getenv("DELIVERY_ASTRO_CONTENT_DIR", "").strip(),
            public_dir=os.getenv("DELIVERY_ASTRO_PUBLIC_DIR", "").strip(),
            assets_dir=os.getenv("DELIVERY_ASTRO_ASSETS_DIR", "").strip(),
        ),
    )
    return DeliveryDispatcher(config=config, outbox_dir=outbox_dir)


def run_once() -> dict[str, Any]:
    """Deliver pending payload files once and exit."""
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
    validate_env_for_runtime(runtime_role=RUNTIME_ROLE_DELIVERY_WORKER)

    outbox_dir = Path(os.getenv("ROADTOCORE_OUTBOX_DIR", "./outbox"))
    outbox_dir.mkdir(parents=True, exist_ok=True)

    dispatcher = _build_delivery_dispatcher(outbox_dir)
    results = deliver_pending_outbox(dispatcher=dispatcher, outbox_dir=outbox_dir)

    delivered = sum(1 for item in results if item.get("status") == "delivered")
    failed = sum(1 for item in results if item.get("status") == "failed")

    return {
        "status": "ok",
        "total": len(results),
        "delivered": delivered,
        "failed": failed,
    }


def main() -> None:
    result = run_once()
    print(
        "[DELIVERY] "
        f"total={result['total']} "
        f"delivered={result['delivered']} "
        f"failed={result['failed']}"
    )


if __name__ == "__main__":
    main()
