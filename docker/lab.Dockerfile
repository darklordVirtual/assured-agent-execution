# AAE Lab — the demonstration and test surface.
#
# A SEPARATE IMAGE from the console, for two reasons that both matter.
#
# Credentials: this one holds every role token and can propose, approve and
# execute. The console holds one viewer token and can do none of it. The
# console's job is to report whether the deployment can be trusted, and a
# process that can approve its own proposals cannot credibly report
# `console_access: read-only` about itself.
#
# Contents: the console image deliberately carries no REMORA package — there is
# a test asserting it — because a read-only dashboard has no business shipping
# the governance engine it displays. The lab runs the benchmark suites, which
# need the SDK. Sharing one image would mean either breaking that property or
# leaving the benchmarks unrunnable, so the two images differ.
#
# Not part of a production profile. Nothing else depends on this image.

FROM python:3.12-slim@sha256:646fb0bca3dd3ea1bcc6feb72c17ed16eed6e10cffc732fcc1478bd3e7f02d7b

LABEL org.opencontainers.image.title="AAE Lab" \
      org.opencontainers.image.description="Demonstration and test surface: compose a governed tool call, act in any role, read the full decision envelope, and run the governance benchmark suites. Holds every role token and has no login. Not for production." \
      org.opencontainers.image.source="https://github.com/darklordVirtual/assured-agent-execution" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Stian Skogbrott"

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 \
 && rm -rf /var/lib/apt/lists/*

# The same pinned wheel the control plane runs, verified in-image against the
# same lock. The lab must speak to the engine through the SDK a real integrator
# would use — a lab on a different client would demonstrate a different system.
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
if actual != lock["wheel"]["sha256"]:
    sys.exit(f"REFUSING TO BUILD: {wheel.name} is not the pinned artifact.")
print(f"pinned core verified in-image: {wheel.name} @ {actual[:16]}...")
PY

COPY docker/requirements.lock /tmp/requirements.lock
RUN pip install --upgrade pip \
 && pip install -c /tmp/requirements.lock \
      "/tmp/core/remora-0.10.0-py3-none-any.whl" \
 && pip install -c /tmp/requirements.lock fastapi "uvicorn[standard]" httpx \
 && rm -rf /tmp/core /tmp/requirements.lock

# The product package (for the benchmark runner), the declared cases, the
# deployment's own ToolPack declarations, and the lab itself.
COPY src/aae /app/aae
COPY benchmarks /app/benchmarks
COPY toolpacks /app/toolpacks
COPY console/lab.py /app/console/lab.py
COPY console/static /app/console/static

RUN touch /app/console/__init__.py \
 && useradd --create-home --uid 10002 lab \
 && chown -R lab:lab /app
USER lab

ENV PYTHONPATH=/app

EXPOSE 8090

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/', timeout=4)" || exit 1

CMD ["uvicorn", "console.lab:app", "--host", "0.0.0.0", "--port", "8090"]
