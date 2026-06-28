from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core.telegram.client import DownloadedTelegramFile
from core.telegram.events import TelegramIntakeEventStore
from core.telegram.webhook import AudioEvent, BufferedImage, TelegramBufferStore
from core.workers import intake_processor


class TelegramStoreTests(unittest.TestCase):
    def test_intake_event_store_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outbox_dir = Path(tmp) / "outbox"
            store = TelegramIntakeEventStore(outbox_dir)

            audio = AudioEvent(chat_id=10, message_id=20, file_id="audio-file", file_unique_id="uniq-a")
            images = [
                BufferedImage(
                    message_id=21,
                    file_id="image-file",
                    file_unique_id="uniq-img",
                    width=640,
                    height=480,
                )
            ]

            first = store.stage_audio_intake(audio, images, idempotency_key="key-12345678", telegram_update_id=500)
            second = store.stage_audio_intake(audio, images, idempotency_key="key-12345678", telegram_update_id=500)

            self.assertEqual(first.status, "staged")
            self.assertEqual(second.status, "duplicate")
            self.assertEqual(first.intake_id, second.intake_id)
            self.assertTrue(Path(first.event_path).exists())

    def test_buffer_store_persists_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            buffer_dir = Path(tmp) / "intake" / "buffer"
            first_store = TelegramBufferStore(buffer_dir)
            second_store = TelegramBufferStore(buffer_dir)

            first_store.add_images(
                100,
                [
                    BufferedImage(
                        message_id=7,
                        file_id="img-1",
                        file_unique_id="img-uniq-1",
                        width=1200,
                        height=900,
                    )
                ],
            )

            peeked = second_store.peek_images(100)
            self.assertEqual(len(peeked), 1)
            self.assertEqual(peeked[0].file_id, "img-1")

            popped = second_store.pop_images(100)
            self.assertEqual(len(popped), 1)
            self.assertEqual(second_store.peek_images(100), [])


class IntakeProcessorTests(unittest.TestCase):
    def _stage_event(self, outbox_dir: Path, idempotency_key: str) -> str:
        store = TelegramIntakeEventStore(outbox_dir)
        audio = AudioEvent(chat_id=99, message_id=123, file_id="audio-file-id", file_unique_id="audio-uniq")
        images = [
            BufferedImage(
                message_id=122,
                file_id="image-file-id",
                file_unique_id="img-uniq",
                width=1280,
                height=720,
            )
        ]
        result = store.stage_audio_intake(audio, images, idempotency_key=idempotency_key, telegram_update_id=42)
        return result.event_path

    @staticmethod
    def _payload(idempotency_key: str) -> dict:
        return {
            "schema_version": "1.1.0",
            "event_id": "11111111-1111-1111-1111-111111111111",
            "idempotency_key": idempotency_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "platform": "telegram",
                "chat_id": 99,
                "audio_message_id": 123,
                "audio_file_id": "audio-file-id",
                "audio_file_unique_id": "audio-uniq",
                "image_message_ids": [122],
            },
            "content": {
                "title": "Test Title",
                "summary": "Summary",
                "transcript_summary": "Transcript summary",
                "transcript_full": "Full transcript",
                "sections": [
                    {
                        "heading": "Section",
                        "level": 2,
                        "paragraphs": ["Paragraph"],
                        "bullet_points": [],
                    }
                ],
            },
            "assets": {
                "images": [
                    {
                        "asset_ref": "telegram://image-file-id",
                        "caption": "",
                        "alt": "",
                        "width": 1280,
                        "height": 720,
                    }
                ]
            },
            "meta": {
                "language": "Italian",
                "tags": [],
                "date": datetime.now(timezone.utc).isoformat(),
            },
            "targets": {
                "wordpress": {"post_status": "draft"},
                "astro": {"draft": True},
            },
            "ai_meta": {
                "provider": "test",
                "model": "transcribe:test|generate:test",
                "latency_ms": 1,
                "token_usage": {"input": 1, "output": 1},
            },
        }

    def test_run_once_processes_staged_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outbox_dir = Path(tmp) / "outbox"
            outbox_dir.mkdir(parents=True, exist_ok=True)
            self._stage_event(outbox_dir, idempotency_key="idem-12345678")

            class FakeTelegramClient:
                def __init__(self, _token: str) -> None:
                    pass

                def download_file(self, file_id: str) -> DownloadedTelegramFile:
                    if file_id == "audio-file-id":
                        return DownloadedTelegramFile(
                            file_id=file_id,
                            file_path="audio.ogg",
                            content=b"OggSstub-audio",
                            mime_type="audio/ogg",
                        )
                    return DownloadedTelegramFile(
                        file_id=file_id,
                        file_path="image.jpg",
                        content=b"fake-image-bytes",
                        mime_type="image/jpeg",
                    )

            class FakePipeline:
                def process(self, **kwargs):
                    return IntakeProcessorTests._payload(kwargs["idempotency_key"])

            with mock.patch.dict(
                os.environ,
                {
                    "TELEGRAM_TOKEN": "test-token",
                    "ROADTOCORE_OUTBOX_DIR": str(outbox_dir),
                    "ROADTOCORE_ASSETS_DIR": str(outbox_dir / "assets"),
                },
                clear=False,
            ), mock.patch.object(intake_processor, "TelegramClient", FakeTelegramClient), mock.patch.object(
                intake_processor, "_load_pipeline", return_value=FakePipeline()
            ):
                result = intake_processor.run_once(max_events=1)

            self.assertEqual(result["processed"], 1)
            payload_path = outbox_dir / "11111111-1111-1111-1111-111111111111.json"
            self.assertTrue(payload_path.exists())

            marker_path = outbox_dir / "intake" / ".idempotency" / "idem-12345678.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(marker.get("status"), "payload_ready")

            processed_events = list((outbox_dir / "intake" / "processed").glob("*.json"))
            self.assertEqual(len(processed_events), 1)

    def test_run_once_marks_failed_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outbox_dir = Path(tmp) / "outbox"
            outbox_dir.mkdir(parents=True, exist_ok=True)
            self._stage_event(outbox_dir, idempotency_key="idem-87654321")

            class FakeTelegramClient:
                def __init__(self, _token: str) -> None:
                    pass

                def download_file(self, file_id: str) -> DownloadedTelegramFile:
                    return DownloadedTelegramFile(
                        file_id=file_id,
                        file_path="audio.ogg",
                        content=b"OggSstub-audio",
                        mime_type="audio/ogg",
                    )

            class FailingPipeline:
                def process(self, **_kwargs):
                    raise RuntimeError("boom")

            with mock.patch.dict(
                os.environ,
                {
                    "TELEGRAM_TOKEN": "test-token",
                    "ROADTOCORE_OUTBOX_DIR": str(outbox_dir),
                    "ROADTOCORE_ASSETS_DIR": str(outbox_dir / "assets"),
                },
                clear=False,
            ), mock.patch.object(intake_processor, "TelegramClient", FakeTelegramClient), mock.patch.object(
                intake_processor, "_load_pipeline", return_value=FailingPipeline()
            ):
                result = intake_processor.run_once(max_events=1)

            self.assertEqual(result["failed"], 1)
            failed_events = list((outbox_dir / "intake" / "failed").glob("*.json"))
            self.assertEqual(len(failed_events), 1)

            marker_path = outbox_dir / "intake" / ".idempotency" / "idem-87654321.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(marker.get("status"), "processing_failed")

            error_files = list((outbox_dir / "intake" / "failed").glob("*.error.txt"))
            self.assertEqual(len(error_files), 1)


if __name__ == "__main__":
    unittest.main()
