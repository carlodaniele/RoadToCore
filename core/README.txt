Run RoadToCode core locally:
1) Copy .env.example to .env and configure values.
2) Install dependencies: pip install -r requirements.txt
3) Start API: uvicorn core.main:app --reload --host 0.0.0.0 --port 8080

Telegram webhook endpoint:
POST /webhook/telegram

Generated payloads are written into ROADTOCODE_OUTBOX_DIR.
