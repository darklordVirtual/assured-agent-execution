# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Sign this deployment's ToolSpec bundle.

A ToolSpec is the authority to call a tool: its argument schema, the
environments it may touch, the credentials it may use, whether its effect can
be read back. If an agent could edit that, governance would be checking the
agent's own claims about itself. So the bundle is signed with a key the
*deployment* holds, and the agent signs nothing.

Usage::

    python scripts/sign_toolpack.py            # sign, write, print the digest
    python scripts/sign_toolpack.py --check    # verify the signed bundle only

What this script does that a hand-written JSON file cannot: it computes each
``callable_digest`` from the SOURCE of the function actually registered in
``toolpacks/work_order/registry.py``. The bundle therefore attests what is
deployed, not what someone remembered to type.

Two honest limitations, both recorded here rather than discovered later:

1. **The digest is a source span.** It cannot see closure variables or module
   globals the callable reads — a function whose source is unchanged but whose
   captured state was swapped verifies clean. This is the frozen contract's own
   documented blindness (``callable_digest_basis: source_span``), not a
   shortcut taken here.

2. **REMORA 0.10.0 records ``callable_digest`` but does not check it.**
   ``ToolSpecBundle.verify_callable`` exists and nothing calls it at dispatch
   time. Computing the real digest is still worth doing — it is true, it is
   signed, and it becomes enforced the moment core wires the check — but until
   then a swapped callable is caught by the policy bundle hash over the module
   source, not by this field.

And one about the signing model itself: HMAC is symmetric, so the process that
verifies holds the key that signs. "The agent cannot sign its own spec" is
therefore only as strong as the agent's inability to read the control plane's
environment. The frozen contract defers asymmetric signing to v2 and says so;
this is the same limitation, named where an operator will see it.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SPEC_SOURCE = ROOT / "toolpacks" / "work_order" / "tool_specs.json"
SIGNED_OUTPUT = ROOT / "toolpacks" / "work_order" / "tool_specs.signed.json"

#: Key id, not a secret. The trust allowlist names it; rotating means adding a
#: new id, re-signing, and removing the old one — after which every spec signed
#: by it fails closed.
SIGNING_IDENTITY = "aae.work_order_signer/v1"


def _callable_digest(tool_id: str) -> str:
    """SHA-256 over the source of the function actually registered."""
    from toolpacks.work_order import registry

    fn = getattr(registry, tool_id, None)
    if fn is None:
        raise SystemExit(
            f"REFUSING TO SIGN: tool_specs.json declares {tool_id!r}, and "
            f"toolpacks/work_order/registry.py has no such callable. A spec "
            f"attesting a function that does not exist is worse than no spec."
        )
    source = inspect.getsource(fn).encode("utf-8")
    return "sha256:" + hashlib.sha256(source).hexdigest()


def _registered_tool_ids() -> set[str]:
    from toolpacks.work_order import registry

    registered: set[str] = set()
    registry.register_tools(lambda name, _fn: registered.add(name))
    return registered


def build() -> dict:
    document = json.loads(SPEC_SOURCE.read_text(encoding="utf-8"))
    specs = document["tool_specs"]

    declared = {spec["tool_id"] for spec in specs}
    registered = _registered_tool_ids()
    # Both directions. A registered tool with no spec would run unattested;
    # a spec with no tool is an authority nobody can exercise, which rots
    # quietly until someone adds a function to match it.
    if missing := registered - declared:
        raise SystemExit(
            f"REFUSING TO SIGN: these tools are registered for dispatch but "
            f"have no ToolSpec: {sorted(missing)}")
    if extra := declared - registered:
        raise SystemExit(
            f"REFUSING TO SIGN: these ToolSpecs describe tools that are not "
            f"registered: {sorted(extra)}")

    for spec in specs:
        spec["callable_digest"] = _callable_digest(spec["tool_id"])
        spec["signing_identity"] = SIGNING_IDENTITY
    return document


def sign(document: dict, key: str) -> dict:
    from remora.toolcall.toolspec import sign_bundle

    return sign_bundle(
        document, key=key, signing_identity=SIGNING_IDENTITY,
        # Second resolution: the timestamp is inside the signed preimage, so a
        # finer clock would make two signings of identical content produce
        # different bundles and therefore different pinned digests.
        signed_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
    )


def bundle_digest(signed: dict) -> str:
    """The digest the runtime compares REMORA_TOOLSPEC_PINNED_DIGEST against.

    Bare hex, NOT prefixed with "sha256:". Everything else in this codebase
    uses the prefixed form, so the first version of this script emitted one —
    and the control plane refused the bundle as `toolspec_bundle_stale`, which
    reads exactly like a real staleness attack. Matching the comparison
    ``toolspec.py`` actually performs is the only thing that matters here.
    """
    from remora.toolcall.toolspec import canonical_signing_bytes

    return hashlib.sha256(canonical_signing_bytes(signed)).hexdigest()


def load_key() -> str:
    from aae.config import load_env_file

    load_env_file()
    key = os.getenv("REMORA_TOOLSPEC_SIGNING_KEY", "").strip()
    if not key:
        raise SystemExit(
            "REMORA_TOOLSPEC_SIGNING_KEY is not set. Run `python run.py up` (or "
            "scripts/bootstrap_env.py) to generate this installation's keys.")
    return key


def _utf8() -> None:
    """UTF-8 output, from the one shared implementation."""
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "src"))
    from aae._io import force_utf8_output

    force_utf8_output()


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify the existing signed bundle, sign nothing")
    parser.add_argument("--pin-into", metavar="ENV_FILE",
                        help="also write REMORA_TOOLSPEC_PINNED_DIGEST into "
                             "this env file, replacing any previous value")
    args = parser.parse_args()
    key = load_key()

    if args.check:
        from remora.toolcall.toolspec import ToolSpecBundle, ToolSpecRefused

        if not SIGNED_OUTPUT.is_file():
            print(f"no signed bundle at {SIGNED_OUTPUT}; run `python run.py sign`",
                  file=sys.stderr)
            return 1
        signed = json.loads(SIGNED_OUTPUT.read_text(encoding="utf-8"))
        try:
            bundle = ToolSpecBundle.load(
                signed, key=key, trusted_identities=[SIGNING_IDENTITY])
        except ToolSpecRefused as exc:
            print(f"BUNDLE REFUSED: {exc}", file=sys.stderr)
            return 1
        print(f"bundle verified: {len(signed['tool_specs'])} signed spec(s), "
              f"digest {bundle_digest(signed)}")
        for spec in signed["tool_specs"]:
            resolved = bundle.get(spec["tool_id"])
            print(f"  {resolved.tool_id:26s} v{resolved.version}  "
                  f"{resolved.toolspec_hash[:20]}...")
        return 0

    signed = sign(build(), key)
    SIGNED_OUTPUT.write_text(
        json.dumps(signed, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    digest = bundle_digest(signed)
    print(f"signed {len(signed['tool_specs'])} spec(s) -> "
          f"{SIGNED_OUTPUT.relative_to(ROOT)}")
    print(f"bundle digest: {digest}")
    print()
    if args.pin_into:
        env_file = Path(args.pin_into)
        kept = [line for line in
                (env_file.read_text(encoding="utf-8").splitlines()
                 if env_file.is_file() else [])
                if not line.startswith("REMORA_TOOLSPEC_PINNED_DIGEST=")]
        kept += ["",
                 "# Pinned bundle digest. A signature proves the bundle is",
                 "# authentic; it says nothing about whether it is CURRENT. A",
                 "# correctly-signed OLDER bundle — one where a tool was",
                 "# cheaper, or an allowed target wider — is the subtle",
                 "# attack, and this is what refuses it.",
                 f"REMORA_TOOLSPEC_PINNED_DIGEST={digest}"]
        env_file.write_text(chr(10).join(kept) + chr(10), encoding="utf-8")
        print(f"pinned into {env_file}")
        print("restart the control plane for it to take effect: "
              "docker compose up -d --force-recreate control-plane")
        return 0

    print("Pin this digest so a correctly-signed OLD bundle is still refused")
    print("— a signature proves authenticity, never currency:")
    print(f"  REMORA_TOOLSPEC_PINNED_DIGEST={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
