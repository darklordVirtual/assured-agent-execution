# Operations

Runbooks. Design reasoning lives in [architecture.md](architecture.md), what is
enforced in [security-model.md](security-model.md).

`aae` lives in the virtualenv `run.py up` creates; `python -m aae.cli` works
without activating it.

## Signing the ToolSpec bundle

```bash
python run.py sign         # sign, and pin the digest into .env
python run.py check-sign   # verify, sign nothing
docker compose up -d --force-recreate control-plane
```

Run after any change to `registry.py` or `tool_specs.json`. Signing recomputes
each `callable_digest` from the deployed source and refuses if a declared tool
has no callable, or a registered callable has no spec.

`tool_specs.signed.json` is generated, not source: it is gitignored, and every
installation signs with its own key.

**The pinned digest is the part people skip.** A signature proves a bundle is
authentic, never that it is current. A correctly-signed older bundle — one
where a tool was cheaper, or a target wider — passes every signature check.
`run.py sign` writes the pin; a test fails if you are running signed-but-unpinned.

**Rotating the signing identity.** Add the new id to
`REMORA_TOOLSPEC_TRUSTED_IDENTITIES`, re-sign, re-pin, restart, then remove the
old id. After removal every spec signed by it fails closed — the correct blast
radius for a compromised key.

## Rotating secrets

```bash
python scripts/bootstrap_env.py --force
```

Rotating a signing key does not invalidate what it already signed; it makes
those signatures **unverifiable**, which is worse unless you meant it. Export
the evidence you need first. Database volumes keep the old passwords, so a
rotation needs `python run.py reset`.

## Backup and restore

```bash
python run.py backup --out ./backups/2026-08-06
python run.py restore --source ./backups/2026-08-06
```

Both databases and the signed bundle, with a manifest hashing all of it. The
restore verifies every digest before applying anything.

**Signing keys are not in the archive.** An archive holding both the chain and
the key that signs it lets its holder forge a history that verifies perfectly.
Keep keys in a secret manager; if a restore lands without them the chain will
not verify, which is the archive telling you the truth.

`pg_dump` takes a lock on every table, so one session left idle-in-transaction
blocks it. The backup sets `lock_timeout` and names the query to run when it
trips.

## Evidence export

```bash
aae evidence export --out ./evidence <proposal-id> ...
python run.py scenarios --evidence-out ./evidence
```

To verify an archive without this product:

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

`"audit_chain_verified": null` means the chain could not be checked. It never
means clean. Two exports of one proposal differ in exactly one field
(`manifest.exported_at`), so an outer digest identifies *that export*.

## When an effect does not verify

```bash
aae verify-effect <proposal-id> close_work_order wo_id=WO-1202
```

| Status | Act on it? |
|---|---|
| `EFFECT_VERIFIED` | no — the record shows the approved delta |
| `EFFECT_MISMATCH` | **yes** — we looked, and it does not. Exits 1. |
| `EFFECT_UNOBSERVABLE` | no — we could not look. Check the reader; do not compensate. |
| `EFFECT_VERIFIER_FAILED` | no — the reader failed. Same. |
| `EFFECT_UNSUPPORTED` | no — no reader declared. The absence is recorded. |

Only MISMATCH exits non-zero: a job that failed on "we could not look" trains
people to ignore it.

## Reading what happened

```bash
aae lifecycle <proposal-id>          # readable trail
aae lifecycle <proposal-id> --json   # the unabridged record
```

`aae execute` exits non-zero unless the **tool** acted. `outcome=execute` with
`tool_execution.executed=false` means the governed step ran and the tool
failed — often with the grant burned and the state unknown. The CLI prints
both fields for exactly this reason.

## Health

```bash
python run.py doctor
python run.py check-sign
docker compose logs control-plane
```

| Exit code | Meaning |
|---|---|
| 0 | it answered, and the answer is fine |
| 1 | it answered, and the answer is a failure |
| 2 | we could not reach it |
| 78 | configuration is missing or unusable |

## Repeating the demo

```bash
python run.py reseed     # reference work orders back to seeded state
python run.py reset      # stop AND destroy the volumes, chain included
```

`down` stops without destroying. The audit chain is never reseeded.

## Upgrading the pinned core

The procedure, the current values and what each check protects against are in
[The pinned core](pinned-core.md#upgrading).
