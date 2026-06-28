from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from core.telegram.client import DownloadedTelegramFile
from core.telegram.events import TelegramIntakeEventStore
from core.telegram.webhook import TelegramBufferStore
from core.workers import intake_processor


class IntakeIntegrationTests(unittest.TestCase):
    @staticmethod
    def _payload(idempotency_key: str, event_id: str) -> dict:
        return {
            "schema_version": "1.1.0",
            "event_id": event_id,
            "idempotency_key": idempotency_key,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {
                "platform": "telegram",
                "chat_id": 777,
                "audio_message_id": 12,
                "audio_file_id": "audio-id",
                "audio_file_unique_id": "audio-uniq",
                "image_message_ids": [11],
            },
            "content": {
                "title": "Integration Test",
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
                        "asset_ref": "telegram://image-id",
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

    def test_webhook_to_intake_processor_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outbox_dir = Path(tmp) / "outbox"
            outbox_dir.mkdir(parents=True, exist_ok=True)

            import core.main as main_module

            original_buffer_store = main_module.buffer_store
            original_intake_store = main_module.intake_store
            original_allowed_chat_ids = main_module.allowed_chat_ids
            original_webhook_secret = main_module.webhook_secret

            try:
                main_module.buffer_store = TelegramBufferStore(outbox_dir / "intake" / "buffer")
                main_module.intake_store = TelegramIntakeEventStore(outbox_dir)
                main_module.allowed_chat_ids = set()
                main_module.webhook_secret = ""

                client = TestClient(main_module.app)

                photo_update = {
                    "update_id": 101,
                    "message": {
                        "message_id": 11,
                        "chat": {"id": 777},
                        "photo": [
                            {"file_id": "image-thumb", "width": 90, "height": 90},
                            {
                                "file_id": "image-id",
                                "file_unique_id": "image-uniq",
                                "width": 1280,
                                "height": 720,
                            },
                        ],
                    },
                }
                buffered = client.post("/webhook/telegram", json=photo_update)
                self.assertEqual(buffered.status_code, 200)
                self.assertEqual(buffered.json().get("status"), "buffered")

                audio_update = {
                    "update_id": 102,
                    "message": {
                        "message_id": 12,
                        "chat": {"id": 777},
                        "voice": {
                            "file_id": "audio-id",
                            "file_unique_id": "audio-uniq",
                        },
                    },
                }
                staged = client.post("/webhook/telegram", json=audio_update)
                self.assertEqual(staged.status_code, 200)
                self.assertEqual(staged.json().get("status"), "staged")

                staged_events = list((outbox_dir / "intake" / "events").glob("*.json"))
                self.assertEqual(len(staged_events), 1)
                staged_payload = json.loads(staged_events[0].read_text(encoding="utf-8"))
                self.assertEqual(len(staged_payload["media"]["images"]), 1)

                class FakeTelegramClient:
                    def __init__(self, _token: str) -> None:
                        pass

                    def download_file(self, file_id: str) -> DownloadedTelegramFile:
                        if file_id == "audio-id":
                            return DownloadedTelegramFile(
                                file_id=file_id,
                                file_path="audio.ogg",
                                content=b"OggS-audio",
                                mime_type="audio/ogg",
                            )
                        return DownloadedTelegramFile(
                            file_id=file_id,
                            file_path="image.jpg",
                            content=b"image-bytes",
                            mime_type="image/jpeg",
                        )

                class FakePipeline:
                    def process(self, **kwargs):
                        return IntakeIntegrationTests._payload(
                            idempotency_key=kwargs["idempotency_key"],
                            event_id="22222222-2222-2222-2222-222222222222",
                        )

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
                neutral_payload = outbox_dir / "22222222-2222-2222-2222-222222222222.json"
                self.assertTrue(neutral_payload.exists())
            finally:
                main_module.buffer_store = original_buffer_store
                main_module.intake_store = original_intake_store
                main_module.allowed_chat_ids = original_allowed_chat_ids
                main_module.webhook_secret = original_webhook_secret

    def test_polling_to_intake_processor_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outbox_dir = Path(tmp) / "outbox"
            outbox_dir.mkdir(parents=True, exist_ok=True)

            from core.telegram import polling as polling_module

            class FakePollingTelegramClient:
                latest_instance = None

                def __init__(self, _token: str) -> None:
                    self.acknowledged_until = None
                    self.deleted_webhook = False
                    FakePollingTelegramClient.latest_instance = self

                def delete_webhook(self, drop_pending_updates: bool = False) -> None:
                    self.deleted_webhook = not drop_pending_updates

                def get_updates(self, **_kwargs):
                    return [
                        {
                            "update_id": 201,
                            "message": {
                                "message_id": 11,
                                "chat": {"id": 777},
                                "photo": [
                                    {
                                        "file_id": "image-id",
                                        "file_unique_id": "image-uniq",
                                        "width": 1280,
                                        "height": 720,
                                    }
                                ],
                            },
                        },
                        {
                            "update_id": 202,
                            "message": {
                                "message_id": 12,
                                "chat": {"id": 777},
                                "voice": {
                                    "file_id": "audio-id",
                                    "file_unique_id": "audio-uniq",
                                },
                            },
                        },
                    ]

                def acknowledge_updates(self, max_update_id: int) -> None:
                    self.acknowledged_until = max_update_id

            with mock.patch.dict(
                os.environ,
                {
                    "TELEGRAM_TOKEN": "test-token",
                    "ROADTOCORE_OUTBOX_DIR": str(outbox_dir),
                    "TELEGRAM_POLLING_AUTO_DELETE_WEBHOOK": "true",
                    "TELEGRAM_ALLOWED_CHAT_IDS": "",
                },
                clear=False,
            ), mock.patch.object(polling_module, "TelegramClient", FakePollingTelegramClient):
                poll_result = polling_module.run_once()

            self.assertEqual(poll_result["audio_staged"], 1)
            self.assertEqual(poll_result["images_buffered"], 1)
            self.assertEqual(poll_result["acknowledged_until"], 202)
            self.assertTrue(FakePollingTelegramClient.latest_instance.deleted_webhook)

            class FakeProcessorTelegramClient:
                def __init__(self, _token: str) -> None:
                    pass

                def download_file(self, file_id: str) -> DownloadedTelegramFile:
                    if file_id == "audio-id":
                        return DownloadedTelegramFile(
                            file_id=file_id,
                            file_path="audio.ogg",
                            content=b"OggS-audio",
                            mime_type="audio/ogg",
                        )
                    return DownloadedTelegramFile(
                        file_id=file_id,
                        file_path="image.jpg",
                        content=b"image-bytes",
                        mime_type="image/jpeg",
                    )

            class FakePipeline:
                def process(self, **kwargs):
                    return IntakeIntegrationTests._payload(
                        idempotency_key=kwargs["idempotency_key"],
                        event_id="33333333-3333-3333-3333-333333333333",
                    )

            with mock.patch.dict(
                os.environ,
                {
                    "TELEGRAM_TOKEN": "test-token",
                    "ROADTOCORE_OUTBOX_DIR": str(outbox_dir),
                    "ROADTOCORE_ASSETS_DIR": str(outbox_dir / "assets"),
                },
                clear=False,
            ), mock.patch.object(intake_processor, "TelegramClient", FakeProcessorTelegramClient), mock.patch.object(
                intake_processor, "_load_pipeline", return_value=FakePipeline()
            ):
                intake_result = intake_processor.run_once(max_events=1)

            self.assertEqual(intake_result["processed"], 1)
            payload_path = outbox_dir / "33333333-3333-3333-3333-333333333333.json"
            self.assertTrue(payload_path.exists())

    def test_intake_processor_batch_across_multiple_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outbox_dir = Path(tmp) / "outbox"
            outbox_dir.mkdir(parents=True, exist_ok=True)

            store = TelegramIntakeEventStore(outbox_dir)

            first_audio = {
                "chat_id": 777,
                "message_id": 12,
                "file_id": "audio-id-1",
                "file_unique_id": "audio-uniq-1",
            }
            second_audio = {
                "chat_id": 777,
                "message_id": 13,
                "file_id": "audio-id-2",
                "file_unique_id": "audio-uniq-2",
            }

            store.stage_audio_intake(
                audio_event=type("Audio", (), first_audio)(),
                buffered_images=[],
                idempotency_key="idem-batch-1",
                telegram_update_id=301,
            )
            store.stage_audio_intake(
                audio_event=type("Audio", (), second_audio)(),
                buffered_images=[],
                idempotency_key="idem-batch-2",
                telegram_update_id=302,
            )

            class FakeProcessorTelegramClient:
                def __init__(self, _token: str) -> None:
                    pass

                def download_file(self, file_id: str) -> DownloadedTelegramFile:
                    return DownloadedTelegramFile(
                        file_id=file_id,
                        file_path="audio.ogg",
                        content=b"OggS-audio",
                        mime_type="audio/ogg",
                    )

            class FakePipeline:
                def process(self, **kwargs):
                    key = kwargs["idempotency_key"]
                    event_id = (
                        "44444444-4444-4444-4444-444444444441"
                        if key == "idem-batch-1"
                        else "44444444-4444-4444-4444-444444444442"
                    )
                    return IntakeIntegrationTests._payload(idempotency_key=key, event_id=event_id)

            with mock.patch.dict(
                os.environ,
                {
                    "TELEGRAM_TOKEN": "test-token",
                    "ROADTOCORE_OUTBOX_DIR": str(outbox_dir),
                    "ROADTOCORE_ASSETS_DIR": str(outbox_dir / "assets"),
                },
                clear=False,
            ), mock.patch.object(intake_processor, "TelegramClient", FakeProcessorTelegramClient), mock.patch.object(
                intake_processor, "_load_pipeline", return_value=FakePipeline()
            ):
                first_run = intake_processor.run_once(max_events=1)
                second_run = intake_processor.run_once(max_events=1)

            self.assertEqual(first_run["processed"], 1)
            self.assertEqual(second_run["processed"], 1)
            self.assertTrue((outbox_dir / "44444444-4444-4444-4444-444444444441.json").exists())
            self.assertTrue((outbox_dir / "44444444-4444-4444-4444-444444444442.json").exists())

    def test_intake_processor_skips_replayed_payload_ready_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outbox_dir = Path(tmp) / "outbox"
            outbox_dir.mkdir(parents=True, exist_ok=True)

            store = TelegramIntakeEventStore(outbox_dir)
            staged = store.stage_audio_intake(
                audio_event=type(
                    "Audio",
                    (),
                    {
                        "chat_id": 777,
                        "message_id": 12,
                        "file_id": "audio-id",
                        "file_unique_id": "audio-uniq",
                    },
                )(),
                buffered_images=[],
                idempotency_key="idem-replay-1",
                telegram_update_id=401,
            )

            class FakeProcessorTelegramClient:
                def __init__(self, _token: str) -> None:
                    pass

                def download_file(self, file_id: str) -> DownloadedTelegramFile:
                    return DownloadedTelegramFile(
                        file_id=file_id,
                        file_path="audio.ogg",
                        content=b"OggS-audio",
                        mime_type="audio/ogg",
                    )

            class FakePipeline:
                def process(self, **kwargs):
                    return IntakeIntegrationTests._payload(
                        idempotency_key=kwargs["idempotency_key"],
                        event_id="55555555-5555-5555-5555-555555555555",
                    )

            with mock.patch.dict(
                os.environ,
                {
                    "TELEGRAM_TOKEN": "test-token",
                    "ROADTOCORE_OUTBOX_DIR": str(outbox_dir),
                    "ROADTOCORE_ASSETS_DIR": str(outbox_dir / "assets"),
                },
                clear=False,
            ), mock.patch.object(intake_processor, "TelegramClient", FakeProcessorTelegramClient), mock.patch.object(
                intake_processor, "_load_pipeline", return_value=FakePipeline()
            ):
                first_run = intake_processor.run_once(max_events=1)

            self.assertEqual(first_run["processed"], 1)

            processed_event_path = outbox_dir / "intake" / "processed" / Path(staged.event_path).name
            replay_event_path = outbox_dir / "intake" / "events" / Path(staged.event_path).name
            processed_event_path.replace(replay_event_path)

            class ShouldNotRunPipeline:
                def process(self, **_kwargs):
                    raise AssertionError("Pipeline should not be called for payload_ready replay")

            with mock.patch.dict(
                os.environ,
                {
                    "TELEGRAM_TOKEN": "test-token",
                    "ROADTOCORE_OUTBOX_DIR": str(outbox_dir),
                    "ROADTOCORE_ASSETS_DIR": str(outbox_dir / "assets"),
                },
                clear=False,
            ), mock.patch.object(intake_processor, "TelegramClient", FakeProcessorTelegramClient), mock.patch.object(
                intake_processor, "_load_pipeline", return_value=ShouldNotRunPipeline()
            ):
                second_run = intake_processor.run_once(max_events=1)

            self.assertEqual(second_run["skipped"], 1)
            self.assertEqual(second_run["processed"], 0)


if __name__ == "__main__":
    unittest.main()
