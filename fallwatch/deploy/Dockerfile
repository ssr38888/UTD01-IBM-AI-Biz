# Builds natively on LinuxONE (s390x) as well as x86/ARM: every base image and package here is multi-arch.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/fallwatch
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY phone phone
COPY viewer viewer
COPY frontend frontend

RUN useradd --system --uid 10001 fallwatch && mkdir -p data && chown fallwatch data
USER fallwatch
WORKDIR /srv/fallwatch/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
