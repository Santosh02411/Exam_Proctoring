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

RUN mkdir -p instance instance/snapshots instance/recordings
0 --workers 3 --timeout 120 wsgi:app"]
