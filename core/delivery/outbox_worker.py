from __future__ import annotations

from pathlib import Path

from core.delivery.dispatch import DeliveryDispatcher


def deliver_pending_outbox(dispatcher: DeliveryDispatcher, outbox_dir: Path) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    
    pending_payloads = sorted(outbox_dir.glob("*.json"))
    print(f"[OUTBOX] Found {len(pending_payloads)} pending payload(s) to deliver...")

    for idx, payload_path in enumerate(pending_payloads, 1):
        try:
            print(f"[OUTBOX] Delivering payload {idx}/{len(pending_payloads)}: {payload_path.name}...")
            result = dispatcher.dispatch_payload(payload_path)
            results.append({"status": "delivered", "path": result["path"]})
        except Exception as exc:  # pragma: no cover - runtime errors.
            print(f"[OUTBOX] ERROR: Dispatch failed, marking as failed: {exc}")
            failed_path = dispatcher.mark_failed(payload_path, str(exc))
            results.append({"status": "failed", "path": failed_path})

    return results
