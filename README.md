# RoadToCore

RoadToCore is a CMS-agnostic content pipeline that receives Telegram messages, triggers on audio arrival, generates structured editorial payloads via AI, and distributes the result to target adapters (WordPress, Astro, and future CMS/frameworks).

## Architecture

- `core/`: Python webhook + orchestration + AI pipeline + schema validation.
- `shared/schema.json`: versioned contract (`1.1.x`) for all adapters.
- `adapters/wordpress/`: WordPress 7.0+ plugin (REST ingest + Abilities API + block mapping).
- `adapters/astro/`: Node/TypeScript adapter that writes Markdown files.

## Data Contract (V1.1)

The payload envelope includes:

- `schema_version`
- `event_id`
- `idempotency_key`
- `created_at`
- `source`
- `content`
- `assets`
- `meta`
- optional `targets`
- optional `ai_meta`

The authoritative schema is in `shared/schema.json`.

## Core Features Implemented

- Telegram webhook endpoint (`POST /webhook/telegram`).
- Trigger only on audio (`audio` or `voice` Telegram message).
- Image buffering by `chat_id` before audio arrives.
- Telegram file download via Bot API.
- Telegram image download and local asset persistence.
- Two-step AI flow:
  1. audio transcription
  2. transcript -> structured content
- JSON schema validation and normalization.
- File-based idempotency replay protection.
- JSON outbox persistence for downstream adapters.
- Delivery layer with retries/backoff and `.delivered` / `.failed` folders.
- Optional direct adapter delivery (WordPress HTTP + Astro CLI).

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
- `ROADTOCORE_OUTBOX_DIR`
- `ROADTOCORE_ASSETS_DIR`
- `DELIVERY_AUTORUN`
- `DELIVERY_WORDPRESS_*`
- `DELIVERY_ASTRO_*`

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

If you want startup to fail when required keys are missing, set:

```bash
ENV_VALIDATION_STRICT=true
```

Deliver pending outbox payloads manually:

```bash
curl -X POST http://localhost:8080/outbox/deliver
```

## Telegram Webhook Notes

The core expects Telegram updates JSON at:

- `POST /webhook/telegram`

If `TELEGRAM_WEBHOOK_SECRET` is set, the header `X-Telegram-Bot-Api-Secret-Token` must match.

Environment diagnostics endpoint:

- `GET /health/env`

## WordPress Adapter

Path:

- `adapters/wordpress/`

Implemented capabilities:

- REST endpoint: `POST /wp-json/roadtocore/v1/receive`
- Idempotent post create/update via `_roadtocore_idempotency_key`
- Content mapping to Gutenberg blocks (`heading`, `paragraph`, `list`)
- Media ingest from `asset_ref` (`http/https` and absolute local path)
- Optional Ability registration: `roadtocore/publish-post`
- Optional AI-based image alt text enrichment when AI Client is available

### Install

Copy the `adapters/wordpress` folder into your WordPress plugins directory and activate the plugin.

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

## Current Limitations

- Google AI integration requires valid API key and model availability.
- File-based idempotency/outbox state is suitable for single-node deployment. For multi-node production, use Redis/Postgres.
- WordPress media ingestion supports `http/https` and absolute local paths; if your adapters are on separate hosts, expose assets through shared storage or URLs.

## Suggested Next Steps

1. Move idempotency and delivery state to Redis/Postgres for horizontal scaling.
2. Add signed payload delivery to adapters.
3. Add automated integration tests for Telegram -> core -> WordPress/Astro end-to-end.
4. Add observability (structured logs + metrics) per delivery attempt.
