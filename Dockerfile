FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SENTINEL_ENV=production \
    PORT=5000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# run as a non-root user; instance/ holds the SQLite file when no DATABASE_URL is set
RUN useradd --create-home --uid 10001 sentinel \
    && mkdir -p /app/instance \
    && chown -R sentinel:sentinel /app
USER sentinel

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','5000'),timeout=4)"

# 2 workers x 4 threads. Rate limits are per worker (see src/security.py).
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 120 app:app"]
