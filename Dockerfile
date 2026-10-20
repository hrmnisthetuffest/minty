# syntax=docker/dockerfile:1
# Minty in a container. Two targets (docker compose picks one with MINTY_IMAGE_TARGET, see docs/docker.md):
#   core   (default)  the web panel, the control API, text chat, Telegram and the tools. No microphone or speaker.
#   voice             core plus the voice libraries, for Linux with a PipeWire or PulseAudio sound server.
FROM python:3.12-slim AS core
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/data MINTY_NO_ONBOARD=1
# Everything Minty keeps (settings, keys, memory, your profile) lives in /data, a volume. The user is not root.
RUN useradd --uid 1000 --create-home --home-dir /data --shell /usr/sbin/nologin minty
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install .
COPY docker ./docker
RUN chmod +x /app/docker/entrypoint.sh
USER minty
VOLUME ["/data"]
EXPOSE 7778
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7778/', timeout=3)" || exit 1
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["minty"]

FROM core AS voice
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends libportaudio2 pulseaudio-utils ffmpeg playerctl \
 && rm -rf /var/lib/apt/lists/* \
 && pip install ".[voice,music,backup,documents]"
USER minty
