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

FROM python:3.12-slim

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

RUN pip install --upgrade pip \
 && pip install "fastapi>=0.115" "uvicorn[standard]>=0.30" "httpx>=0.27" \
                "psycopg[binary]>=3.1"

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
