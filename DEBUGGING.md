# RoadToCore Workflow Debugging Guide

Quick reference for identifying where anomalies occur in the RoadToCore pipeline.

## Quick Diagnostic Flow

```
START
  ↓
[POLLING] Starting Telegram polling worker...
  ├─ FAIL? → TELEGRAM_TOKEN not set or invalid
  ↓
[POLLING] Ensuring polling mode (deleting webhook)...
  ├─ FAIL? → Telegram API unreachable
  ↓
[POLLING] Fetching Telegram updates...
  ├─ FAIL? → Network timeout, check TELEGRAM_TOKEN
  ├─ "No pending updates" → Normal (nothing to process)
  ↓
[POLLING] Fetched N update(s).
  ├─ Filter by allowed chat IDs
  ↓
  [POLLING] Processing audio: chat=*** message_id=***
    ├─ SKIP? → Duplicate message (idempotency check passed)
    ↓
    [TELEGRAM] Downloading audio file...
      ├─ FAIL? → Invalid file_id, Telegram connectivity issue
      ↓
    [TELEGRAM] Audio downloaded: N bytes
      ├─ FAIL? → Network timeout, file corrupted
      ↓
    [AI] Starting AI pipeline...
      ↓
      [TRANSCRIPTION] Starting transcription (N bytes, mime/type)...
        ├─ FAIL? → 
        │  ├─ GOOGLE_API_KEY invalid/missing
        │  ├─ Gemini API quota exceeded
        │  ├─ Audio format unsupported
        │  └─ API timeout (30s)
        ↓
      [TRANSCRIPTION] Complete (Xms, Y chars, tokens in=Z out=W)
        ├─ Check elapsed time (should be 1-10s for most audio)
        ├─ Check character count (should be > 10 chars for valid audio)
        ↓
      [GENERATION] Starting content generation (N chars, M image(s))...
        ↓
      [GENERATION] Complete (Xms, tokens in=Z out=W)
        ├─ OR: [GENERATION] First attempt returned invalid JSON; retrying...
        │   ├─ FAIL? → Gemini API unstable, retry might succeed
        ├─ OR: [GENERATION] Retry successful (Xms total, tokens...)
        │   ├─ Check if retry succeeded (should always)
        ├─ FAIL? → 
        │  ├─ JSON parsing failed (invalid schema)
        │  ├─ Gemini returned non-JSON response
        │  └─ API timeout (30s)
        ↓
    [AI] Pipeline complete.
      ↓
    [IMAGES] Processing N image(s) from batch...
      ├─ "0 images" → OK (audio-only message)
      ↓
      [IMAGES] Image 1: Downloading from Telegram...
        ├─ FAIL? → Invalid photo_id
        ↓
      [IMAGES] Image 1: Downloaded (N bytes)
        ├─ "0 bytes" → Corrupted download
        ↓
      [IMAGES] Image 1: Extracting EXIF metadata...
        ├─ FAIL? → Unsupported image format
        ↓
      [IMAGES] Image 1: EXIF parsed - GPS(45.1234, 7.5678)
        ├─ OR: No GPS → Image has no GPS EXIF data (normal)
        ↓
      [IMAGES] Image 1: Rotating and optimizing...
        ├─ FAIL? → Corrupted image data, Pillow error
        ↓
      [IMAGES] Image 1: Optimized (N bytes)
        ├─ Check size reduction (should be < original)
        ↓
      [IMAGES] Image 1: Saved to /tmp/.../image-1.jpg
        ├─ FAIL? → Disk write error, permissions issue
        ↓
    [IMAGES] Processed N image(s) with EXIF/GPS extraction.
      ↓
    [VALIDATE] Normalizing and validating payload...
      ├─ FAIL? → Schema mismatch, missing required fields
      ↓
    [VALIDATE] Payload valid.
      ├─ OR: [WARNING] AI generation returned fallback model; skipping delivery.
      │   └─ Payload marked as failed (not delivered)
      ↓
    [POLLING] Saved: UUID — Article Title
      ↓
[DELIVERY] Delivering N payload(s) to WordPress...
  ├─ "No new payloads" → All payloads from previous runs delivered
  ↓
[OUTBOX] Found N pending payload(s) to deliver...
  ↓
[OUTBOX] Delivering payload 1/N: UUID.json...
  ↓
[DISPATCH] Dispatching event UUID...
  ↓
  [DISPATCH] Starting WordPress dispatch for event UUID...
    ├─ FAIL? → WordPress endpoint unreachable
    ↓
    [DISPATCH] Processing N image(s) for upload...
      ↓
      [DISPATCH] Image 1/N: uploading local file...
        ├─ FAIL? → File not found (disk inconsistency)
        ↓
        [WORDPRESS] Uploading image: name (N bytes, mime/type)...
          ├─ FAIL? →
          │  ├─ Authentication failed (bad credentials)
          │  ├─ WordPress /wp-json/wp/v2/media unreachable
          │  ├─ Server disk full
          │  └─ Timeout (30s)
          ↓
        [WORDPRESS] Image uploaded: https://... (ID: 123)
          ├─ Check URL is valid and accessible
          ↓
    [DISPATCH] Building gallery blocks for N image(s)...
      ├─ FAIL? → Image metadata missing or malformed
      ↓
    [DISPATCH] Gallery blocks appended.
      ↓
    [DISPATCH] Sending payload to WordPress endpoint...
      ├─ FAIL? →
      │  ├─ REST API error (bad JSON, invalid post type)
      │  ├─ Authentication failed
      │  └─ Timeout (30s)
      ↓
    [DISPATCH] WordPress dispatch successful (HTTP 200).
      ↓
[DISPATCH] delivered: /tmp/roadtocore_outbox/.delivered/UUID.json
  ↓
[POLLING] Acknowledged N update(s) up to UPDATE_ID.
  ├─ FAIL? → Telegram acknowledgment API error (rare)
  ↓
[POLLING] Workflow complete.
  ↓
END ✓
```

## Common Failure Scenarios

### Scenario 1: Workflow stops at `[TELEGRAM] Downloading audio file...`

**Probable Cause**: Telegram connectivity or invalid file ID

**Remediation**:
1. Check TELEGRAM_TOKEN is valid and not expired
2. Verify network connectivity to api.telegram.org
3. Check GitHub Actions logs for HTTP errors
4. If file_id is very old (> 24h), Telegram may have deleted it

### Scenario 2: Workflow stops at `[TRANSCRIPTION] Starting transcription...`

**Probable Cause**: Google Gemini API issue

**Remediation**:
1. Verify GOOGLE_API_KEY is valid (`Settings → API keys` in Google Cloud console)
2. Check quota/billing in Google Cloud project
3. Verify gemini-2.5-flash model is available in project
4. Check API key permissions include Generative Language API
5. If quota exceeded, wait for reset or increase quota

### Scenario 3: Workflow stops at `[GENERATION] First attempt returned invalid JSON; retrying...` and then stops

**Probable Cause**: Gemini API returning invalid JSON consistently

**Remediation**:
1. Check generation prompt template in `core/ai/prompts/generation.prompt.md`
2. Try reducing word count targets in `core/ai/request_config.json`
3. Check if model is experiencing issues (try different model ID)
4. Check if transcript is very long (> 5000 chars) causing API issues

### Scenario 4: Workflow stops at `[IMAGES] Image X: Extracting EXIF metadata...`

**Probable Cause**: Corrupted image data or unsupported format

**Remediation**:
1. Check image is valid JPEG/PNG/WebP
2. Try re-downloading image from Telegram
3. Check Pillow library is properly installed (pip install Pillow)
4. Check downloaded byte count was > 0

### Scenario 5: Workflow stops at `[WORDPRESS] Uploading image:...`

**Probable Cause**: WordPress credentials, endpoint, or permissions

**Remediation**:
1. Verify DELIVERY_WORDPRESS_ENDPOINT is correct (e.g., https://example.com/wp-json/roadtocore/payload)
2. Verify DELIVERY_WORDPRESS_USERNAME exists and has upload permissions
3. Verify DELIVERY_WORDPRESS_APP_PASSWORD is correct (not regular password!)
4. Test endpoint with curl: `curl -u user:pass https://example.com/wp-json/wp/v2/media`
5. Ensure `/wp-json/wp/v2/media` endpoint is publicly writable with auth

### Scenario 6: Workflow completes but images not in WordPress Media Library

**Probable Cause**: Upload succeeded but images not persisted

**Remediation**:
1. Check WordPress error logs (`wp-content/debug.log`)
2. Verify disk space on WordPress server
3. Check wp-content/uploads directory permissions (should be 755)
4. Verify HTTP 200 in `[DISPATCH] WordPress dispatch successful` message

### Scenario 7: Workflow stops at `[POLLING] Acknowledged N update(s)...`

**Probable Cause**: Rare — Telegram API acknowledgment failed

**Remediation**:
1. Check network connectivity
2. May retry on next polling cycle (safe to ignore if payload delivered)
3. Check GitHub Actions logs for HTTP error details

## Performance Expectations

| Step | Typical Duration | Max Acceptable |
|------|------------------|-----------------|
| Audio Download | 1-5s | 30s |
| Transcription | 2-8s | 30s (API timeout) |
| Generation (first attempt) | 2-6s | 30s (API timeout) |
| Generation (retry if needed) | 2-6s | 30s (API timeout) |
| Image Download (per image) | 1-3s | 30s |
| EXIF + Optimize (per image) | 0.5-1s | 5s |
| WordPress Upload (per image) | 1-3s | 30s |
| Total Workflow | 10-60s | 5min |

If any step exceeds "Max Acceptable", check:
- Network latency (run `ping` from GitHub Actions runner)
- API quotas and rate limits
- Server CPU/disk utilization
- File sizes (larger images/audio take longer)

## Log Levels Reference

| Marker | Meaning | Action If Seen |
|--------|---------|----------------|
| `[MARKER]` | Informational step | None — workflow proceeding normally |
| `[ERROR]` | Hard failure, workflow stops | Check diagnostic section above |
| `[WARNING]` | Non-fatal issue, may be recovered | Check context (e.g., fallback model) |
| `ERROR:` (old format) | Legacy error format | Same as `[ERROR]` |

## Testing Logs Locally

To test the logging system without real Telegram messages:

```bash
# Activate venv
source .venv/bin/activate

# Run polling (will fetch real Telegram updates if TELEGRAM_TOKEN is set)
python -m core.polling

# Or test with empty message list by setting invalid token temporarily
TELEGRAM_TOKEN="invalid" python -m core.polling
```

Expected output for empty updates:
```
[POLLING] Starting Telegram polling worker...
[POLLING] Ensuring polling mode (deleting webhook)...
[ERROR] ...
```

## Accessing GitHub Actions Logs

1. Go to repository → "Actions" tab
2. Click on "Telegram Poll" workflow
3. Click on the specific run
4. Click "Poll Telegram and deliver to WordPress" step
5. Expand "Run python -m core.polling"
6. Search for `[MARKER]` to navigate quickly

---

**Last Updated**: 2026-06-22 (v0.2.0)
