# Pick knihovna — the friend's page between jams, on the host's PC. Build from the app's root:
#   docker build -f deploy/pick-library/Dockerfile -t pick-library .
FROM python:3.12.7-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir fastapi==0.103.2 uvicorn==0.23.2 starlette==0.27.0 \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin pick

WORKDIR /app
COPY deploy/pick-library/library.py /app/library.py
COPY amp_catalog.py /app/amp_catalog.py
COPY static/jam.html static/pick-favicon.png static/jam-icon-192.png static/jam-icon-512.png static/jam-icon.ico static/jam-launcher.cmd /app/static/
COPY static/jam-engine.js static/jam-worklet.js static/jam-guest.js static/jam-chat.js static/jam-mix.js static/jam-amp.js static/jam-sw.js /app/static/
COPY static/stretch /app/static/stretch
COPY static/nam /app/static/nam
COPY static/alphatab /app/static/alphatab

ENV PICK_STATIC=/app/static PICK_DATA=/data/spotdl PICK_AMPS=/data/amps PICK_HELPER=/data/helper PICK_STATE=/state PYTHONDONTWRITEBYTECODE=1
# a new volume takes the owner of the folder it mounts on: the app must be able to write there
RUN mkdir -p /state && chown 10001:10001 /state
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"
CMD ["uvicorn", "library:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
