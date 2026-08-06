# The AAE control plane: REMORA's governance API, from the pinned wheel.
#
# The dependency direction this product rests on is visible in this file.
# There is no REMORA checkout, no submodule, no `pip install git+...`. The
# build takes ONE input from the core — the wheel named in
# product/core-artifact-lock.json — and installs it. `servers/` and `schemas/`
# ship inside that wheel (REM-045), so running the control plane is deploying
# the core, not importing it: no AAE source file imports a REMORA internal
# namespace, and tests/compatibility enforces that separately.
#
# The wheel must already be in dist/, put there by:
#
#     python scripts/verify_core_pin.py --out dist
#
# and this build verifies its digest AGAIN, inside the image, against the lock
# it also copies in. Twice looks redundant and is not: the first check gates
# the download, the second makes the image self-describing. An image built
# from a wheel nobody verified fails its own build rather than shipping.

FROM python:3.12-slim

LABEL org.opencontainers.image.title="Assured Agent Execution — Control Plane" \
      org.opencontainers.image.description="REMORA governance API in production mode, execution surface only. Fail-closed prerequisites (durable execution state, durable envelope store, token table with role separation, signing keys) are binding at startup. No oracle, no retrieval store, no network egress." \
      org.opencontainers.image.source="https://github.com/darklordVirtual/assured-agent-execution" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Stian Skogbrott"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libpq for psycopg: durable execution state and the control-plane envelope
# store are both Postgres here, and both are fail-closed prerequisites.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/*

# --- The pinned core, verified before it is trusted -------------------------
COPY product/core-artifact-lock.json /app/product/core-artifact-lock.json
COPY dist/remora-0.10.0-py3-none-any.whl /tmp/core/

RUN python - <<'PY'
import hashlib, json, pathlib, sys

lock = json.loads(
    pathlib.Path("/app/product/core-artifact-lock.json").read_text("utf-8"))
wheel = pathlib.Path("/tmp/core") / lock["wheel"]["filename"]
if not wheel.is_file():
    sys.exit(f"REFUSING TO BUILD: {wheel.name} is not in the build context. "
             f"Run: python scripts/verify_core_pin.py --out dist")
actual = hashlib.sha256(wheel.read_bytes()).hexdigest()
expected = lock["wheel"]["sha256"]
if actual != expected:
    sys.exit(f"REFUSING TO BUILD: {wheel.name} is not the pinned artifact.\n"
             f"  expected {expected}\n  got      {actual}")
print(f"pinned core verified in-image: {wheel.name} @ {actual[:16]}...")
PY

RUN pip install --upgrade pip \
 && pip install "/tmp/core/remora-0.10.0-py3-none-any.whl[api,postgres]" \
 && rm -rf /tmp/core

# --- The product's own deployment-owned modules -----------------------------
# These are the two things a deployment writes itself. They are AAE source and
# import no REMORA internal namespace; the semantic bundle's declared
# dependency on remora.toolcall is pinned by a compatibility test.
COPY toolpacks /app/toolpacks
COPY docker/entrypoint.sh /usr/local/bin/aae-entrypoint

RUN chmod +x /usr/local/bin/aae-entrypoint \
 && useradd --create-home --uid 10001 aae \
 && mkdir -p /var/lib/aae/artifacts \
 && chown -R aae:aae /var/lib/aae /app
USER aae

ENV PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=25s --retries=6 \
  CMD curl -fsS http://127.0.0.1:8000/v1/health || exit 1

ENTRYPOINT ["aae-entrypoint"]
CMD ["uvicorn", "servers.api:app", "--host", "0.0.0.0", "--port", "8000"]
