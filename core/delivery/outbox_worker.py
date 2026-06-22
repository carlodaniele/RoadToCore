from __future__ import annotations

from pathlib import Path

from core.delivery.dispatch import DeliveryDispatcher


def deliver_pending_outbox(dispatcher: DeliveryDispatcher, outbox_dir: Path) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    
    pending_payloads = sorted(outbox_dir.glob("*.json"))
    print(f\"[OUTBOX] Found {len(pending_payloads)} pending payload(s) to deliver...\")\n\n    for idx, payload_path in enumerate(pending_payloads, 1):\n        try:\n            print(f\"[OUTBOX] Delivering payload {idx}/{len(pending_payloads)}: {payload_path.name}...\")\n            result = dispatcher.dispatch_payload(payload_path)\n            results.append({\"status\": \"delivered\", \"path\": result[\"path\"]})\n        except Exception as exc:  # pragma: no cover - runtime errors.\n            print(f\"[OUTBOX] ERROR: Dispatch failed, marking as failed: {exc}\")\n            failed_path = dispatcher.mark_failed(payload_path, str(exc))\n            results.append({\"status\": \"failed\", \"path\": failed_path})\n\n    return results
