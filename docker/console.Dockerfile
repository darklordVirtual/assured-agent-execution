# AAE Assurance Console — read-only.
#
# A separate, deliberately small image: fastapi, httpx, psycopg. No REMORA
# wheel, no product package.
#
# It used to install both, because it shelled out to the product CLI to run
# the scenarios. Moving that to the CLI — where a privileged credential
# belongs — let the whole dependency go with it. A read-only dashboard has no
# business carrying the governance engine it displays.
#
# The point of keeping it separate: nothing about the demonstration surface
# can change the behaviour of the system being demonstrated.

# Base image pinned by digest, not by tag. `python:3.12-slim` is a moving
# target: the same Dockerfile built a week apart produced different images, and
# the tag would not have said so. Update deliberately with
# `docker buildx imagetools inspect python:3.12-slim`.
FROM python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b

LABEL org.opencontainers.image.title="AAE Assurance Console" \
      org.opencontainers.image.description="Read-only assurance console: enforcement status, governed decisions and business records, on a viewer token and a SELECT-only database credential. Holds no policy, makes no decisions, and exposes no route that writes." \
      org.opencontainers.image.source="https://github.com/darklordVirtual/assured-agent-execution" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Stian Skogbrott"

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /console

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

COPY docker/requirements.lock /tmp/requirements.lock
RUN pip install --upgrade pip \
 && pip install -r /tmp/requirements.lock \
 && rm -f /tmp/requirements.lock

# The artifact lock, and nothing else from product/. It is 30 lines of JSON
# naming which core release this deployment verified — the console reports it
# on the assurance surface, and reported it EMPTY once the wheel was removed
# from this image and the file went with it.
#
# Copying it back does not reintroduce the REMORA dependency: it is data the
# console reads, not a package it imports.
COPY product/core-artifact-lock.json /console/core-artifact-lock.json
COPY console/app.py /console/app.py
COPY console/static /console/static

RUN useradd --create-home --uid 10001 console && chown -R console:console /console
USER console

EXPOSE 8090

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/', timeout=4)" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
