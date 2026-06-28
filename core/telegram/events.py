from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.telegram.webhook import AudioEvent, BufferedImage


@dataclass
class TelegramIntakeEvent:
    schema_version: str
    event_type: str
    intake_id: str
    created_at: str
    idempotency_key: str
    source: dict[str, Any]
    media: dict[str, Any]
    context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IntakeStageResult:
    status: str
    intake_id: str
    event_path: str
    idempotency_key: str


class TelegramIntakeEventStore:
    """Filesystem-backed staging store for Telegram intake events."""

    def __init__(self, outbox_dir: Path) -> None:
        self.outbox_dir = outbox_dir
        self.intake_dir = outbox_dir / "intake"
        self.events_dir = self.intake_dir / "events"
        self.idempotency_dir = self.intake_dir / ".idempotency"

        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.idempotency_dir.mkdir(parents=True, exist_ok=True)

    def _idempotency_file(self, idempotency_key: str) -> Path:
        return self.idempotency_dir / f"{idempotency_key}.json"

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def stage_audio_intake(
        self,
        audio_event: AudioEvent,
        buffered_images: list[BufferedImage],
        idempotency_key: str,
        telegram_update_id: int | None = None,
    ) -> IntakeStageResult:
        marker_path = self._idempotency_file(idempotency_key)
        if marker_path.exists():
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except Exception:
                marker = {}

            intake_id = str(marker.get("intake_id", "")).strip()
            event_path = str(marker.get("event_path", "")).strip()
            if intake_id and event_path:
                return IntakeStageResult(
                    status="duplicate",
                    intake_id=intake_id,
                    event_path=event_path,
                    idempotency_key=idempotency_key,
                )

        intake_id = str(uuid4())
        created_at = datetime.now(timezone.utc).isoformat()

        event = TelegramIntakeEvent(
            schema_version="1.0.0",
            event_type="telegram.intake.audio",
            intake_id=intake_id,
            created_at=created_at,
            idempotency_key=idempotency_key,
            source={
                "platform": "telegram",
                "chat_id": audio_event.chat_id,
                "message_id": audio_event.message_id,
                "update_id": telegram_update_id,
            },
            media={
                "audio": {
                    "file_id": audio_event.file_id,
                    "file_unique_id": audio_event.file_unique_id,
                },
                "images": [asdict(image) for image in buffered_images],
            },
            context={
                "status": "intake_staged",
            },
        )

        event_path = self.events_dir / f"{intake_id}.json"
        self._write_json_atomic(event_path, event.to_dict())
        self._write_json_atomic(
            marker_path,
            {
                "status": "intake_staged",
                "intake_id": intake_id,
                "event_path": str(event_path),
            },
        )

        return IntakeStageResult(
            status="staged",
            intake_id=intake_id,
            event_path=str(event_path),
            idempotency_key=idempotency_key,
        )
