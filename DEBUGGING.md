# RoadToCore Debugging Guide (Decoupled Pipeline)

This guide reflects the staged architecture:

1. ingest stage (webhook)
2. polling stage (Telegram -> staged intake events)
3. intake-processor stage (staged intake -> neutral payload JSON)
4. delivery-worker stage (neutral payload -> adapters)

## Stage-First Triage

When something fails, identify the first failing stage and debug only that boundary.

1. Ingest healthy, polling healthy, intake failing:
   focus on AI/media/schema processing.
2. Intake healthy, delivery failing:
   focus on adapter credentials/endpoints/content mapping.
3. Polling failing:
   focus on Telegram token/chat filter/webhook mode.

## Runtime Roles and Checks

Use role-aware checks before running a stage.

```bash
python3 -m core.check_github_env ingest
python3 -m core.check_github_env polling
python3 -m core.check_github_env intake-processor
python3 -m core.check_github_env delivery-worker
```

If your shell does not expose `python`, use `python3` or the venv executable.

## Canonical Commands

Run one stage at a time.

```bash
python3 -m core.telegram.polling
python3 -m core.workers.intake_processor
```

The scheduler workflow runs those two commands in sequence, with separate env validation for each role.

## Log Markers by Stage

- `[POLLING]`: polling worker lifecycle and counters.
- `[INTAKE]`: intake processor summary counters.
- `[OUTBOX]`: delivery worker queue and dispatch attempts.
- `[DISPATCH]` and `[WORDPRESS]`: adapter-level delivery flow.

Treat these markers as stage ownership boundaries.

## Filesystem Signals

Check these paths to understand current state:

- `outbox/intake/events`: staged, waiting for intake processor.
- `outbox/intake/processed`: intake events successfully converted.
- `outbox/intake/failed`: intake conversion failures.
- `outbox/intake/.idempotency`: idempotency and processing status.
- `outbox/*.json`: neutral payloads awaiting delivery.

## Common Failure Cases

### 1) Polling returns zero staged events unexpectedly

Probable causes:

- `TELEGRAM_ALLOWED_CHAT_IDS` excludes sender chat.
- message has no supported audio payload.
- Telegram updates already acknowledged by another consumer.

Checks:

- confirm chat filter configuration.
- confirm update contains `audio`, `voice`, or audio `document`.
- confirm only one poller is active.

### 2) Intake processor fails on Telegram media download

Probable causes:

- `TELEGRAM_TOKEN` invalid.
- Telegram file no longer available.
- transient network failure.

Checks:

- rerun intake processor after validating role env.
- inspect latest file in `outbox/intake/failed` and matching marker in `.idempotency`.

### 3) Intake processor fails on AI generation/transcription

Probable causes:

- `GOOGLE_API_KEY` missing or invalid.
- provider quota/model availability issue.
- malformed upstream transcript/generation response.

Checks:

- run `python3 -m core.check_github_env intake-processor`.
- inspect `.error.txt` alongside failed intake event.

### 4) Delivery worker fails with payloads stuck in outbox

Probable causes:

- WordPress endpoint or credentials invalid.
- adapter runtime dependency mismatch.

Checks:

- run role validation for delivery worker.
- inspect `[DISPATCH]` and `[WORDPRESS]` logs.
- verify adapter secrets and endpoint reachability.

## GitHub Actions Notes

The poll workflow is intentionally decoupled:

1. validate polling role
2. run polling stager (`core.telegram.polling`)
3. validate intake-processor role
4. run intake processor (`core.workers.intake_processor`)
5. upload outbox artifact for inspection

This means a successful polling step does not imply successful delivery.

## Fast Local Validation

```bash
python3 -m unittest core.tests.test_intake_refactor core.tests.test_intake_integration -v
```

Use these tests to validate intake boundaries without running external delivery targets.

---

Last Updated: 2026-06-28
