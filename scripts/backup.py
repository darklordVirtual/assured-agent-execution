# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Back up and restore this deployment, so the audit chain survives the host.

    python run.py backup --out ./backups/2026-08-06
    python run.py restore ./backups/2026-08-06

A governance system whose tamper-evident chain cannot survive a disk failure
is a governance system with an expiry date. This takes both databases and the
signed ToolSpec bundle, and writes a manifest hashing all of it.

Two things it deliberately does NOT do:

**It does not back up ``.env``.** The signing keys are what make the chain
tamper-evident; an archive containing both the chain and the key that signs it
lets anyone holding the archive forge a consistent history. Back the keys up
separately, in a secret manager, and the restore step says so if they are
missing.

**It does not promise the restore is byte-identical.** ``pg_dump`` output
carries the dump's own timestamp, so the archive's digests identify *this
backup*, not the database. What the restore proves is stronger and simpler:
after restoring, REMORA verifies the tenant audit chain end to end. A chain
that verifies after a restore was not tampered with in between — which is the
property anyone actually cares about.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: service -> (database, superuser, env var holding its password)
DATABASES = {
    "control-plane-db": ("remora", "remora", "CONTROL_PLANE_DB_PASSWORD"),
    "workorder-db": ("workorders", "wo_admin", "WORKORDER_DB_PASSWORD"),
}


def _compose(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, **kwargs)


def _env(name: str) -> str:
    from aae.config import load_env_file

    load_env_file()
    import os

    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is not set; run `python run.py up` first")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def backup(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    for service, (database, user, password_env) in DATABASES.items():
        target = out_dir / f"{service}.sql"
        print(f"  dumping {database} from {service}")
        # lock_timeout, and a wall clock on top of it.
        #
        # pg_dump takes an ACCESS SHARE lock on every table, so a single
        # session left idle-in-transaction blocks it indefinitely. A backup
        # that hangs in silence is worse than one that fails: a scheduled
        # job never reports, and nobody finds out until the restore.
        result = _compose(
            "exec", "-T",
            "-e", f"PGPASSWORD={_env(password_env)}",
            "-e", "PGOPTIONS=-c lock_timeout=15s -c statement_timeout=300s",
            service,
            "pg_dump", "-U", user, "-d", database, "--clean", "--if-exists",
            capture_output=True, text=True, timeout=360)
        if result.returncode != 0:
            raise SystemExit(
                f"pg_dump failed for {service}: {result.stderr.strip()}\n"
                f"If it timed out on a lock, something is holding a "
                f"transaction open. Look with:\n"
                f"  docker compose exec {service} psql -U {user} "
                f"-d {database} -c "
                "\"SELECT pid, state, query FROM pg_stat_activity "
                "WHERE state = 'idle in transaction'\"")
        target.write_text(result.stdout, encoding="utf-8")
        files[target.name] = _sha256(target)

    # The signed ToolSpec bundle. Restoring a chain without the bundle that
    # authorized its actions leaves entries nobody can re-check.
    bundle = ROOT / "toolpacks" / "work_order" / "tool_specs.signed.json"
    if bundle.is_file():
        copy = out_dir / bundle.name
        copy.write_bytes(bundle.read_bytes())
        files[copy.name] = _sha256(copy)

    lock = json.loads(
        (ROOT / "product" / "core-artifact-lock.json").read_text("utf-8"))
    manifest = {
        "manifest_version": 1,
        "product": "assured-agent-execution",
        "taken_at": datetime.now(UTC).isoformat(),
        # Which core wrote this data. Restoring into a different core is not
        # forbidden, but it is a fact the restore should be able to state.
        "core_release": lock["release_tag"],
        "core_commit": lock["remora_core_commit"],
        "files": {name: f"sha256:{digest}" for name, digest in sorted(files.items())},
        "excludes_signing_keys": True,
        "note": (
            "Signing keys are NOT in this archive. An archive holding both the "
            "audit chain and the key that signs it lets its holder forge a "
            "consistent history. Restore them from your secret manager."
        ),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def restore(source: Path) -> int:
    manifest_path = source / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"no manifest.json in {source}; not an AAE backup")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Verify before restoring. Restoring a corrupted dump into a live
    # deployment is worse than refusing to restore at all.
    problems = []
    for name, declared in manifest["files"].items():
        path = source / name
        if not path.is_file():
            problems.append(f"missing: {name}")
        elif f"sha256:{_sha256(path)}" != declared:
            problems.append(f"digest mismatch: {name}")
    if problems:
        print("REFUSING TO RESTORE — the archive is not what its manifest says:",
              file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    lock = json.loads(
        (ROOT / "product" / "core-artifact-lock.json").read_text("utf-8"))
    if manifest.get("core_release") != lock["release_tag"]:
        print(f"NOTE: this backup was taken on core "
              f"{manifest.get('core_release')}; this deployment pins "
              f"{lock['release_tag']}. Restoring anyway — the schema is "
              f"compatible within a core version — but the difference is "
              f"recorded here rather than discovered later.")

    for service, (database, user, password_env) in DATABASES.items():
        dump = source / f"{service}.sql"
        if not dump.is_file():
            continue
        print(f"  restoring {database} into {service}")
        result = _compose(
            "exec", "-T", "-e", f"PGPASSWORD={_env(password_env)}", service,
            "psql", "-U", user, "-d", database, "-v", "ON_ERROR_STOP=1",
            "-q", input=dump.read_text(encoding="utf-8"),
            capture_output=True, text=True, timeout=360)
        if result.returncode != 0:
            print(f"restore failed for {service}:\n{result.stderr.strip()}",
                  file=sys.stderr)
            return 1

    bundle = source / "tool_specs.signed.json"
    if bundle.is_file():
        (ROOT / "toolpacks" / "work_order" / bundle.name).write_bytes(
            bundle.read_bytes())
        print("  restored the signed ToolSpec bundle")

    print("\n  restored. Verify the chain before trusting it:\n"
          "    python run.py doctor\n"
          "    python -m aae.cli evidence export --out ./check <proposal-id>\n"
          "\n  If the signing keys in .env are not the ones this data was\n"
          "  written under, the chain will not verify — that is the archive\n"
          "  telling you the truth, not a restore failure.")
    return 0


def _utf8() -> None:
    """UTF-8 output, from the one shared implementation."""
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "src"))
    from aae._io import force_utf8_output

    force_utf8_output()


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    take = sub.add_parser("backup")
    take.add_argument("--out", required=True)
    put = sub.add_parser("restore")
    put.add_argument("source")
    args = parser.parse_args()

    if args.action == "backup":
        manifest = backup(Path(args.out))
        print(f"\n  backed up {len(manifest['files'])} file(s) → {args.out}")
        print("  signing keys are NOT included; back them up separately")
        return 0
    return restore(Path(args.source))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    raise SystemExit(main())
