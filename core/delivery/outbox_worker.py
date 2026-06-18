from __future__ import annotations

from pathlib import Path

from core.delivery.dispatch import DeliveryDispatcher


def deliver_pending_outbox(dispatcher: DeliveryDispatcher, outbox_dir: Path) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []

    for payload_path in sorted(outbox_dir.glob("*.json")):
        try:
            result = dispatcher.dispatch_payload(payload_path)
            results.append({"status": "delivered", "path": result["path"]})
        except Exception as exc:  # pragma: no cover - runtime errors.
            failed_path = dispatcher.mark_failed(payload_path, str(exc))
            results.append({"status": "failed", "path": failed_path})

    return results
