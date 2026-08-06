# Operator console for Assured Agent Execution.
#
# A separate, deliberately small image. It installs the pinned REMORA wheel
# only because the product CLI it shells out to needs the SDK — the console's
# own code imports no remora module at all, and holds no policy.
#
# The point of keeping it separate: nothing about the demonstration surface
# can change the behaviour of the system being demonstrated. If the console
# and the control plane shared a process, "the console says it works" would
# be a claim about one program, not two.

FROM python:3.12-slim

LABEL org.opencontainers.image.title="Assured Agent Execution — Operator Console" \
      org.opencontainers.image.description="Read-mostly operator surface: deployment posture, the four decisions, and the system of record on a read-only credential. Holds no policy and makes no decisions." \
      org.opencontainers.image.source="https://github.com/darklordVirtual/assured-agent-execution" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Stian Skogbrott"

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /console

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

# The same hash-verified wheel the control plane runs, verified again here.
COPY product/core-artifact-lock.json /console/core-artifact-lock.json
COPY dist/remora-0.10.0-py3-none-any.whl /tmp/core/

RUN python - <<'PY'
import hashlib, json, pathlib, sys

lock = json.loads(pathlib.Path("/console/core-artifact-lock.json").read_text("utf-8"))
wheel = pathlib.Path("/tmp/core") / lock["wheel"]["filename"]
actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
if actual != lock["wheel"]["sha256"]:
    sys.exit(f"REFUSING TO BUILD: {wheel.name} is not the pinned artifact")
print(f"pinned core verified in-image: {wheel.name} @ {actual[:16]}...")
PY

RUN pip install --upgrade pip \
 && pip install "fastapi>=0.115" "uvicorn[standard]>=0.30" "httpx>=0.27" \
                "psycopg[binary]>=3.1" \
 && pip install "/tmp/core/remora-0.10.0-py3-none-any.whl[sdk]" \
 && rm -rf /tmp/core

# The product package, so the console runs the SAME scenarios the CLI does
# rather than its own copy of them.
COPY pyproject.toml README.md /console/
COPY src /console/src
COPY toolpacks /console/toolpacks
COPY product /console/product
RUN pip install --no-deps -e /console

COPY console/app.py /console/app.py

RUN useradd --create-home --uid 10001 console && chown -R console:console /console
USER console

EXPOSE 8090

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/', timeout=4)" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
