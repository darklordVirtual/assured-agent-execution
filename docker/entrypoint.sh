#!/bin/sh
# Preflight for the AAE control plane.
#
# REMORA's own fail-closed checks run at import and are the authority on
# whether the process may serve. This adds the two things it cannot know,
# because they are facts about THIS product's configuration:
#
#   1. the deployment-owned modules resolve;
#   2. the tool classification and the work-order authority file are readable.
#
# Both would otherwise fail later and less clearly: an unresolvable registry
# module surfaces as every dispatch reporting unknown_tool, and an unreadable
# authority file surfaces as every proposal abstaining. Both look like policy
# refusing to work rather than like a mount that is missing.
#
# Refusing here costs one container start. Not refusing costs an operator an
# afternoon deciding whether the governance engine is broken.

set -eu

fail() {
    echo "AAE control plane refusing to start: $1" >&2
    exit 78   # EX_CONFIG
}

[ -n "${REMORA_TOOL_REGISTRY_MODULE:-}" ] \
    || fail "REMORA_TOOL_REGISTRY_MODULE is not set; no tool could be dispatched"
[ -n "${REMORA_SEMANTIC_BUNDLE_MODULE:-}" ] \
    || fail "REMORA_SEMANTIC_BUNDLE_MODULE is not set; nothing would ground a call"

python - <<'PY' || exit 78
import importlib, json, os, pathlib, sys

for env_name in ("REMORA_TOOL_REGISTRY_MODULE", "REMORA_SEMANTIC_BUNDLE_MODULE"):
    spec = os.environ[env_name]
    try:
        importlib.import_module(spec)
    except Exception as exc:
        print(f"AAE control plane refusing to start: {env_name}={spec} does "
              f"not import ({type(exc).__name__}: {exc})", file=sys.stderr)
        raise SystemExit(1)

metadata = os.environ.get("REMORA_TOOL_METADATA_FILE", "").strip()
if metadata:
    try:
        declared = json.loads(pathlib.Path(metadata).read_text("utf-8"))["tools"]
    except Exception as exc:
        print(f"AAE control plane refusing to start: tool metadata at "
              f"{metadata} is unreadable ({exc})", file=sys.stderr)
        raise SystemExit(1)
    print(f"tool classification: {len(declared)} tool(s) declared")

authority = os.environ.get("AAE_WORK_ORDER_AUTHORITY_FILE", "").strip()
if authority:
    try:
        table = json.loads(pathlib.Path(authority).read_text("utf-8"))
    except Exception as exc:
        print(f"AAE control plane refusing to start: work-order authority "
              f"file at {authority} is unreadable ({exc})", file=sys.stderr)
        raise SystemExit(1)
    # Underscore keys are commentary, not work orders.
    count = sum(1 for k in table if not k.startswith("_"))
    if count == 0:
        print("AAE control plane refusing to start: the work-order authority "
              "file declares no work orders, so no proposal could ever be "
              "grounded", file=sys.stderr)
        raise SystemExit(1)
    print(f"work-order authority: {count} signed work order(s)")
PY

exec "$@"
