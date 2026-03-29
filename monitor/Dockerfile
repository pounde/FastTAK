FROM python:3.12-alpine

WORKDIR /app

COPY requirements.txt .
RUN apk add --no-cache openssl curl && pip install --no-cache-dir -r requirements.txt

COPY config/ ./config/
COPY app/ ./app/

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/ping')"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
