# Exam Proctoring — production image
FROM python:3.12-slim

# opencv-python-headless still needs a couple of system libs at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p instance instance/snapshots instance/recordings \
    && chmod +x scripts/docker-entrypoint.sh

ENV FLASK_APP=wsgi.py \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# SECRET_KEY, APP_BASE_URL, MAIL_*, DATABASE_URL ... are REQUIRED environment
# variables in production (APP_ENV defaults to production): the app refuses to
# start without them — see app/config_validation.py and .env.example.
#
# The entrypoint applies migrations (web service only) and aborts the
# container if they fail. Background jobs do NOT run in the web workers:
# run the separate `scheduler` service from docker-compose.yml.
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "wsgi:app"]
