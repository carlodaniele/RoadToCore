# RoadToCore

RoadToCore is a CMS-agnostic content pipeline with a decoupled intake architecture: Telegram input stages events first, then downstream workers process AI and delivery independently.

## Architecture

- `core/`: Python API + Telegram intake modules + AI pipeline + schema validation.
- `shared/schema.json`: versioned contract (`1.1.x`) for all adapters.
- `adapters/wordpress/`: WordPress 7.0+ plugin (REST ingest + Abilities API + block mapping).
- `adapters/astro/`: Node/TypeScript adapter that writes Markdown files.

## Data Contract (V1.1)

The payload envelope includes:

- `schema_version` (format: `1.1.x`)
- `event_id` (UUID)
- `idempotency_key` (unique per event)
- `created_at` (ISO 8601 timestamp)
- `source` (Telegram platform info + chat/audio/image IDs)
- `content` (title, summary, transcript, structured sections)
- `assets.images` (array with `asset_ref`, `url`, `alt`, `caption`, and optional **`gps` object** with latitude/longitude)
- `meta` (language, tags, date)
- optional `targets` (WordPress/Astro specific settings)
- optional `ai_meta` (provider, model, latency, token usage)

**GPS Metadata**: Image assets now include optional `gps` field extracted from EXIF:

```json
"gps": {
  "latitude": 45.1234,
  "longitude": 7.5678
}
```

The authoritative schema is in `shared/schema.json`.

## Core Features Implemented

- Telegram webhook ingestion (`POST /webhook/telegram`) with fast, non-blocking intake staging.
- Telegram polling worker with strict short-lived `run_once` behavior.
- Trigger on audio (`audio`, `voice`, or `document` with audio MIME type).
- Persistent per-chat image buffering on filesystem (`outbox/intake/buffer`).
- Intake event staging on filesystem (`outbox/intake/events`).
- File-based intake idempotency (`outbox/intake/.idempotency`).
- Shared Telegram parsing and idempotency key logic for webhook and polling paths.
- Delivery and AI execution are intentionally decoupled from input layer runtime.
- Intake processor worker (`run_once`) that consumes staged Telegram events and emits schema-validated neutral payloads.
- Scheduled GitHub Actions runtime that executes polling + intake processor as decoupled stages.

## Core Setup

### 1) Configure environment

Copy and edit:

- `core/.env.example` -> `core/.env`

Important variables:

- `TELEGRAM_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `TELEGRAM_ALLOWED_CHAT_IDS`
- `AI_PROVIDER` (`google`)
- `GOOGLE_API_KEY`
- `GOOGLE_TRANSCRIPTION_MODEL`
- `GOOGLE_GENERATION_MODEL`
- `ROADTOCORE_SYSTEM_PROMPT_FILE`
- `ROADTOCORE_GENERATION_PROMPT_FILE`
- `ROADTOCORE_AI_REQUEST_CONFIG_FILE`
- `ROADTOCORE_OUTBOX_DIR`
- `ROADTOCORE_ASSETS_DIR`
- `ROADTOCORE_RUNTIME_ROLE` (`ingest`, `polling`, `intake-processor`, `delivery-worker`, `all`)
- `DELIVERY_WORDPRESS_*`
- `DELIVERY_ASTRO_*`

### Prompt Customization (System + Generation)

RoadToCore supports external prompt files so you can tune behavior without code changes.

- Default system prompt: `core/ai/prompts/system.prompt.md`
- Default generation prompt: `core/ai/prompts/generation.prompt.md`

Environment variables:

- `ROADTOCORE_SYSTEM_PROMPT_FILE`
- `ROADTOCORE_GENERATION_PROMPT_FILE`
- `ROADTOCORE_AI_REQUEST_CONFIG_FILE`

The generation prompt template supports these placeholders:

- `{{SYSTEM_PROMPT}}`
- `{{LANGUAGE}}`
- `{{SCHEMA_HINT_JSON}}`
- `{{TRANSCRIPT}}`

If prompt files are missing or empty, the pipeline falls back to internal default prompts.

### AI Request Parameters Configuration

RoadToCore can also externalize request-level AI parameters (for example temperature) in JSON.

- Default file: `core/ai/request_config.json`
- Env var: `ROADTOCORE_AI_REQUEST_CONFIG_FILE`

Example:

```json
{
  "transcription": {
    "temperature": 0.1,
    "top_p": 0.95
  },
  "generation": {
    "temperature": 0.7,
    "top_p": 0.9,
    "top_k": 40,
    "max_output_tokens": 1200,
    "target_words": 500
  }
}
```

Notes:

- `transcription` applies to the audio transcription request.
- `generation` applies to structured content generation.
- `target_words` controls approximate total output length (for example `500` or `5000`).
- You can use `min_words` and/or `max_words` as an alternative range-based constraint.
- If the file is missing/invalid, RoadToCore safely falls back to default provider behavior.

#### Practical examples

For an output around 500 words:

```json
{
  "generation": {
    "temperature": 0.7,
    "max_output_tokens": 1200,
    "target_words": 500
  }
}
```

For an output around 5000 words:

```json
{
  "generation": {
    "temperature": 0.7,
    "max_output_tokens": 9000,
    "target_words": 5000
  }
}
```

Important:

- Keep `max_output_tokens` high enough for long outputs, otherwise the model may truncate.
- `target_words`, `min_words`, and `max_words` are editorial controls used in the prompt; they are not forwarded to provider API config.

### 2) Install dependencies

```bash
cd core
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3) Run API

```bash
cd ..
uvicorn core.main:app --reload --host 0.0.0.0 --port 8080
```

Health check:

```bash
curl http://localhost:8080/health
```

Validate required environment variables (useful in GitHub Actions):

```bash
cd core
python -m core.check_github_env
```

Validate required environment variables for a specific runtime role:

```bash
python -m core.check_github_env ingest
python -m core.check_github_env polling
python -m core.check_github_env intake-processor
python -m core.check_github_env delivery-worker
```

If you want startup to fail when required keys are missing, set:

```bash
ENV_VALIDATION_STRICT=true
```

Role-aware validation notes:

- `ingest` validates only ingest-role requirements.
- `polling` validates Telegram polling requirements.
- `intake-processor` validates Telegram + AI requirements.
- `delivery-worker` validates only enabled delivery adapter requirements.
- `all` validates all requirements together.

Run one polling cycle (short-lived worker):

```bash
python -m core.telegram.polling
```

Run one intake processing cycle (short-lived worker):

```bash
python -m core.workers.intake_processor
```

This worker reads staged events from `outbox/intake/events`, downloads Telegram assets, runs the AI pipeline, validates payloads against `shared/schema.json`, and writes neutral payload files into the outbox root.

Run intake refactor regression tests:

```bash
python -m unittest core.tests.test_intake_refactor core.tests.test_intake_integration -v
```

## Image Processing (EXIF Metadata Extraction)

RoadToCore automatically processes images to extract metadata and optimize file size.

### Features

- **GPS coordinate extraction**: reads `GPSInfo` EXIF tags to capture latitude/longitude
- **Image rotation**: corrects orientation based on EXIF `Orientation` tag (common on phone photos)
- **Image optimization**: resizes to 1600px max width + compresses JPEG to quality 85

### EXIF Modules

- `core/output/exif.py`: provides extraction and optimization functions
- Functions: `extract_exif_metadata()`, `rotate_image_by_exif()`, `optimize_image()`

### Example: GPS in payload

When an image with GPS tags arrives:

```json
"assets": {
  "images": [
    {
      "asset_ref": "/tmp/image-1.jpg",
      "gps": {
        "latitude": 45.4642,
        "longitude": 9.1900
      },
      "url": "https://wordpress.example.com/wp-content/uploads/2026/06/image.jpg"
    }
  ]
}
```

The WordPress adapter automatically displays this as:

```
(45.4642, 9.1900)
```

in the image caption.

## Telegram Webhook Notes

The core expects Telegram updates JSON at:

- `POST /webhook/telegram`

If `TELEGRAM_WEBHOOK_SECRET` is set, the header `X-Telegram-Bot-Api-Secret-Token` must match.

Environment diagnostics endpoint:

- `GET /health/env`

## Provider-Agnostic Delivery with GitHub Actions

This repository now includes CI/CD workflows that keep you independent from a specific hosting provider.

- CI workflow: `.github/workflows/ci.yml`
- Release workflow: `.github/workflows/release-artifacts.yml`
- Container image: `ghcr.io/<owner>/roadtocore-core`

### What gets produced

- A Docker image for the core API is built and pushed to GitHub Container Registry (GHCR).
- A ZIP artifact for the WordPress adapter is generated and uploaded in workflow artifacts.

### Why this is hosting-independent

Any provider that supports Docker can run the same immutable image by pulling from GHCR.
You can deploy on VPS, managed container services, Kubernetes, or your own server without changing the build pipeline.

### Suggested production run command

```bash
docker run -d \
  --name roadtocore \
  -p 8080:8080 \
  --env-file /path/to/core.env \
  -v /path/to/outbox:/app/outbox \
  ghcr.io/<owner>/roadtocore-core:latest
```

### Required GitHub settings

- Ensure GitHub Actions is enabled for the repository.
- Ensure package permissions allow publishing to GHCR.
- Keep secrets in GitHub Secrets (for example provider keys used at runtime on your host, not in the image).

## WordPress Adapter

Path:

- `adapters/wordpress/`
- Latest version: **v0.2.0**

Implemented capabilities:

- REST endpoint: `POST /wp-json/roadtocore/v1/receive`
- Idempotent post create/update via `_roadtocore_idempotency_key`
- Content mapping to Gutenberg blocks (`heading`, `paragraph`, `list`)
- **Media upload**: uploads images directly to WordPress Media Library via `/wp/v2/media`
- **Gallery blocks**: automatically builds Gutenberg gallery blocks with lightbox support
- GPS coordinates: embeds location data in figure captions (if available in EXIF)
- Optional Ability registration: `roadtocore/publish-post`
- Optional AI-based image alt text enrichment when AI Client is available

### Image Processing in WordPress Adapter

When images arrive:

1. **Direct Media Library upload**: bypasses local asset URLs, ensures images are stored in WordPress
2. **Gallery block generation**: creates proper Gutenberg blocks (`wp:gallery`, `wp:image`)
3. **GPS display**: if EXIF GPS coordinates are present, they appear in the image caption as `(latitude, longitude)`
4. **Featured image**: automatically sets the first uploaded image as featured image

### Install

Copy the `adapters/wordpress` folder into your WordPress plugins directory and activate the plugin.

To get the latest version:

- Download `adapters/roadtocore-adapter-v0.2.0.zip` from the repo
- Upload via WordPress Admin → Plugins → Add New → Upload Plugin

## Astro Adapter

Path:

- `adapters/astro/`

Implemented capabilities:

- Reads payload JSON file
- Renders Markdown with frontmatter
- Writes output in collection folder
- Copies local image assets into Astro public directory

### Setup

```bash
cd adapters/astro
npm install
npm run build
```

### Run

```bash
node dist/index.js \
  --input /absolute/path/to/payload.json \
  --content-dir /absolute/path/to/astro/src/content \
  --public-dir /absolute/path/to/astro/public/images/roadtocore \
  --assets-dir /absolute/path/to/local/assets
```

## Debugging & Monitoring

RoadToCore now runs as a decoupled staged pipeline. Troubleshooting should be done per runtime role instead of treating polling as a monolithic "poll + AI + delivery" loop.

Runtime stages:

- `ingest`: webhook endpoint only (stages intake events).
- `polling`: Telegram polling stager only (`run_once`).
- `intake-processor`: consumes staged events, downloads media, runs AI, emits neutral payload JSON.
- `delivery-worker`: dispatches pending outbox payloads to enabled adapters.

For full troubleshooting procedures and failure mapping by stage, see `DEBUGGING.md`.

## Current Limitations

- Google AI integration requires valid API key and model availability.
- File-based idempotency/outbox state is suitable for single-node deployment. For multi-node production, use Redis/Postgres.
- Polling runs every 5 minutes via GitHub Actions cron and stages events first; AI processing runs in a dedicated intake processor step.
- EXIF GPS extraction depends on image having valid GPS tags; images without GPS metadata will have no `gps` field in payload.

## Suggested Next Steps

1. Move idempotency and delivery state to Redis/Postgres for horizontal scaling.
2. Implement CI/CD auto-deploy for WordPress adapter (currently manual zip upload).
3. Add signed payload delivery to adapters.
4. Add automated integration tests for Telegram → core → WordPress/Astro end-to-end.
5. Add observability (structured logs + metrics) per delivery attempt.
6. Support video audio extraction (ffmpeg) for video files.
7. Add interview transcription mode (speaker detection + Q&A format).
