# Operations

Signing, rotation, evidence, and what to do when something does not verify.

## Signing the ToolSpec bundle

```bash
make sign        # sign, and pin the digest into .env
make check-sign  # verify the current bundle, sign nothing
```

`make sign` computes each `callable_digest` from the source of the function
actually registered, and **refuses to sign** if a declared tool has no
callable or a registered callable has no spec. Both directions matter: a
registered tool with no spec runs unattested; a spec with no tool is an
authority nobody can exercise, which rots quietly until someone adds a
function to match it.

Run it after any change to `registry.py` or `tool_specs.json`, then restart:

```bash
docker compose up -d --force-recreate control-plane
```

### The pinned digest

`REMORA_TOOLSPEC_PINNED_DIGEST` is the part people skip, and it is the one
that stops the interesting attack.

A signature proves the bundle is **authentic**. It says nothing about whether
it is **current**. A correctly-signed *older* bundle — one where a tool was
cheaper, or an allowed target wider — passes every signature check, because it
really was signed by this deployment. Only the pin refuses it.

A deployment with a bundle configured and no pin has exactly that hole, and
nothing about it looks different. `tests/e2e/test_toolspec.py::test_the_running_deployment_pins_a_digest`
fails if you are in that state.

### Rotating the signing identity

Rotation and revocation are the same mechanism: the trust allowlist.

1. Add the new identity to `REMORA_TOOLSPEC_TRUSTED_IDENTITIES` (comma-separated).
2. Re-sign with the new identity, re-pin, restart.
3. Remove the old identity.

After step 3 every spec signed by the old identity fails closed — immediate
and total, which is the correct blast radius for a compromised signing key.

### What HMAC signing does not give you

HMAC is symmetric: the process that **verifies** the bundle holds the key that
**signs** it. "The agent cannot sign its own spec" is therefore only as strong
as the agent's inability to read the control plane's environment.

The frozen ToolSpec contract defers asymmetric/KMS signing to v2 and says so.
If your threat model includes an agent that can read control-plane
environment variables, this control does not hold for you.

## Rotating secrets

```bash
python scripts/bootstrap_env.py --force
```

Read the warning it prints before you do.

Rotating a signing key does not invalidate what it already signed — it makes
those signatures **unverifiable**, which is worse than either alternative
unless you meant it. Export the evidence you need to keep first:

```bash
aae evidence export --out ./evidence-before-rotation <proposal-id> ...
```

Existing database volumes also keep the OLD passwords. After a rotation you
need `make down` (which removes them) or the stack will not start.

## Evidence export

```bash
aae evidence export --out ./evidence <proposal-id> ...
aae scenarios --evidence-out ./evidence     # export what the scenarios produced
```

The archive contains, per proposal, REMORA's bundle **exactly as returned**
(its manifest hashes those precise JSON bytes, so re-shaping them here would
break independent verification), the lifecycle trail, the chain verification
taken at export time, and a manifest hashing every file.

Two things to know when reading one:

- `"audit_chain_verified": null` means the chain **could not be checked**. It
  never means clean.
- Two exports of the same proposal are not byte-identical. Exactly one field
  moves — REMORA stamps `manifest.exported_at` into each bundle — so an outer
  file digest identifies *that export*, not the evidence. The sections
  themselves are stable.

To verify an archive you were handed, without this product:

```bash
python - <<'EOF'
import hashlib, json, pathlib
root = pathlib.Path("evidence")
manifest = json.loads((root / "manifest.json").read_text())
for name, declared in manifest["files"].items():
    actual = "sha256:" + hashlib.sha256((root / name).read_bytes()).hexdigest()
    print(f"{'OK ' if actual == declared else 'BAD'} {name}")
EOF
```

## When an effect does not verify

The status tells you what kind of problem you have, and only one of them is a
statement about the action.

**`EFFECT_MISMATCH` — terminal.** We looked, and the system of record does not
show the approved delta. This needs a human. It is the only status this
product may act on.

**`EFFECT_UNOBSERVABLE` — not terminal.** The reader could not see the object.
This is not evidence the action failed. The incident stays open. Check the
reader's connectivity and credential; do not compensate.

**`EFFECT_VERIFIER_FAILED` — not terminal.** The reader itself failed. Also
not a statement about the action. Same treatment.

**`EFFECT_UNSUPPORTED` — not terminal.** No postcondition reader is declared
for this tool, so nothing confirms the effect happened. Recorded so the
absence is visible — a tool nobody can verify and a tool that verified must
never look the same afterwards.

Re-run a verification by hand:

```bash
aae verify-effect <proposal-id> close_work_order wo_id=WO-1202
```

Exit code 1 only on `EFFECT_MISMATCH`. The unknowns exit 0, because a CI job
must not treat "we could not look" as a failure of the action.

## Health and diagnosis

```bash
aae doctor                      # pin, surfaces, reachability, reader credential
make check-sign                 # the ToolSpec bundle
docker compose logs control-plane
docker compose ps -a
```

`aae doctor` checks the reader credential **as a reader** — it connects and
confirms it can read. A doctor that only proved connectivity would miss the
property that matters.

Exit codes across the CLI are kept distinct on purpose:

| Code | Meaning |
|---|---|
| 0 | the system answered, and the answer is fine |
| 1 | the system answered, and the answer is a failure |
| 2 | we could not reach it — a different fact from a failure |
| 78 | configuration is missing or unusable |

## Upgrading the pinned core

```bash
# 1. edit product/core-artifact-lock.json — tag, commit, wheel digest
# 2. fetch the release's manifest and pin its digest too
gh release download <tag> -R darklordVirtual/REMORA-research \
   -D dist -p core-release-manifest.json --clobber
cp dist/core-release-manifest.json product/core-release-manifest.json
# 3.
python scripts/verify_core_pin.py --out dist
make compat        # the lock and the manifest must agree field by field
make up && make verify
```

`tests/compatibility/test_pin_manifest_agreement.py` compares the hand-written
lock against the release-generated manifest — commit, version, every artifact
digest, the symbol count three ways, and the consumer contract. It runs on
JSON only, so a disagreement fails before anything is downloaded or built.

That test exists because the in-tree manifest was stale for two pin bumps —
naming commit `f3e58db` and 28 SDK symbols while the lock had moved to
`4c85937` and 36 — and nothing noticed, because nothing compared them.
