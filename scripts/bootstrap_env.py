# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Generate this installation's secrets into .env, once.

Every AAE install gets its own signing keys, database passwords and bearer
tokens. Nothing is committed, nothing is shared, and there is no
``change-me`` default to forget — a compose file with placeholder keys in it
teaches people that keys are decoration, and the first deployment that keeps
them is the one where the audit chain stops meaning anything.

Idempotent by design: an existing .env is never overwritten, because doing so
would silently invalidate every audit entry and execution lease already signed
under the old keys. Rotation is a deliberate act (``--force``), and the
consequences are printed rather than assumed understood.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

#: name -> (bytes of entropy, comment)
_SECRETS = {
    "REMORA_PDP_SIGNING_KEY": (32, "signs policy decision tokens"),
    "REMORA_LEASE_SIGNING_KEY": (32, "signs execution leases"),
    "REMORA_AUDIT_SIGNING_KEY": (32, "signs tenant audit chain entries"),
    "REMORA_ENVELOPE_SIGNING_KEY": (32, "signs DecisionEnvelopes"),
    "CONTROL_PLANE_DB_PASSWORD": (18, "governance state database"),
    "WORKORDER_DB_PASSWORD": (18, "system-of-record admin (migrations only)"),
    "AAE_WORKER_PASSWORD": (18, "system-of-record writer — the tool callables"),
    "AAE_READER_PASSWORD": (18, "system-of-record reader — SELECT only"),
    "AAE_TOKEN_AGENT": (16, "operator role: may propose and execute, never approve"),
    "AAE_TOKEN_REVIEWER": (16, "reviewer: may approve routine holds, never execute"),
    "AAE_TOKEN_EXPERT": (16, "domain_expert: may approve production writes"),
    "AAE_TOKEN_SENIOR": (16, "senior_authority: may approve escalations"),
    "AAE_TOKEN_VIEWER": (16, "viewer role: may read, never act"),
}

_HEADER = """\
# Assured Agent Execution — generated secrets for THIS installation.
#
# Written once by scripts/bootstrap_env.py. Not committed, not shared, and not
# derived from anything. Every value below is independent randomness.
#
# Rotating a signing key does not invalidate what it already signed — it makes
# those signatures unverifiable, which is worse than either alternative unless
# you meant it. Export the evidence you need to keep BEFORE rotating:
#
#     aae evidence export --out ./evidence
#
# In a real deployment these come from a secret manager and this file does not
# exist.
"""


def _generate() -> str:
    lines = [_HEADER]
    for name, (entropy, comment) in _SECRETS.items():
        lines.append(f"\n# {comment}\n{name}={secrets.token_urlsafe(entropy)}")
    lines.append("\n\n# Host port for the control plane API.\nAAE_API_PORT=8080\n")
    return "".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force", action="store_true",
        help="replace an existing .env — see the warning it prints first")
    args = parser.parse_args()

    if ENV_FILE.exists() and not args.force:
        print(f".env already exists, leaving it alone ({ENV_FILE})")
        return 0

    if ENV_FILE.exists():
        print(
            "WARNING: replacing .env rotates every signing key.\n"
            "  Audit entries, execution leases and DecisionEnvelopes signed "
            "under the old keys become UNVERIFIABLE — not invalid, "
            "unverifiable, which no later export can repair.\n"
            "  Existing database volumes also keep the OLD passwords; run "
            "`make down` (which removes them) or this stack will not start.",
            file=sys.stderr,
        )

    ENV_FILE.write_text(_generate(), encoding="utf-8")
    try:
        ENV_FILE.chmod(0o600)  # no-op on Windows, correct everywhere else
    except OSError:
        pass
    print(f"wrote {len(_SECRETS)} generated secrets to {ENV_FILE}")
    print("this installation shares no credential with any other")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
