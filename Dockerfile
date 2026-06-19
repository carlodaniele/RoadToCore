FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY core/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY core /app/core
COPY shared /app/shared

EXPOSE 8080

CMD ["uvicorn", "core.main:app", "--host", "0.0.0.0", "--port", "8080"]
