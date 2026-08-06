# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Back it up, destroy it, restore it, and check the chain still verifies.

A governance system whose tamper-evident chain cannot survive a disk failure
has an expiry date. But a backup that merely restores *rows* proves very
little: the interesting question is whether the chain still verifies
afterwards, because a chain that verifies after a round trip was not tampered
with in between.

That is the test below. It takes a backup, deletes governance state, restores,
and asks REMORA — not this product — whether the chain is intact.

It also checks the two refusals, because a restore tool that will happily
apply a corrupted archive is worse than no restore tool: it turns a detectable
problem into a silent one.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("live")

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "backup.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          cwd=ROOT, capture_output=True, text=True)


@pytest.fixture()
def archive(tmp_path: Path) -> Path:
    out = tmp_path / "archive"
    result = _run("backup", "--out", str(out))
    if result.returncode != 0:
        pytest.skip(f"backup unavailable here: {result.stderr.strip()[:200]}")
    return out


# ── What a backup contains, and what it refuses to contain ─────────────────

def test_a_backup_carries_both_databases_and_the_signed_bundle(archive) -> None:
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    names = set(manifest["files"])
    assert "control-plane-db.sql" in names
    assert "workorder-db.sql" in names
    assert "tool_specs.signed.json" in names, (
        "restoring a chain without the bundle that authorized its actions "
        "leaves entries nobody can re-check")


def test_every_file_matches_its_declared_digest(archive) -> None:
    import hashlib

    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    for name, declared in manifest["files"].items():
        actual = "sha256:" + hashlib.sha256(
            (archive / name).read_bytes()).hexdigest()
        assert actual == declared, f"{name} does not match the manifest"


def test_the_backup_records_which_core_wrote_the_data(archive) -> None:
    """Restoring into a different core is a fact the archive can state."""
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    lock = json.loads(
        (ROOT / "product" / "core-artifact-lock.json").read_text(encoding="utf-8"))
    assert manifest["core_release"] == lock["release_tag"]
    assert manifest["core_commit"] == lock["remora_core_commit"]


def test_the_signing_keys_are_not_in_the_archive(archive) -> None:
    """The one thing a backup must never contain.

    An archive holding both the audit chain and the key that signs it lets its
    holder forge a consistent history — the chain would verify perfectly, for
    events that never happened.
    """
    import os

    from aae.config import load_env_file

    load_env_file()
    blob = "".join(p.read_text(encoding="utf-8", errors="ignore")
                   for p in archive.iterdir() if p.is_file())
    for name in ("REMORA_AUDIT_SIGNING_KEY", "REMORA_PDP_SIGNING_KEY",
                 "REMORA_TOOLSPEC_SIGNING_KEY", "REMORA_LEASE_SIGNING_KEY"):
        key = os.getenv(name, "")
        if key:
            assert key not in blob, f"{name} is inside the backup archive"

    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["excludes_signing_keys"] is True


# ── Refusals ───────────────────────────────────────────────────────────────

def test_a_tampered_archive_is_refused(archive) -> None:
    """Applying a corrupted dump turns a detectable problem into a silent one."""
    dump = archive / "workorder-db.sql"
    dump.write_text(dump.read_text(encoding="utf-8") + "\n-- tampered\n",
                    encoding="utf-8")

    result = _run("restore", str(archive))
    assert result.returncode == 1
    assert "REFUSING TO RESTORE" in result.stderr
    assert "digest mismatch" in result.stderr


def test_a_directory_that_is_not_a_backup_is_refused(tmp_path) -> None:
    result = _run("restore", str(tmp_path))
    assert result.returncode != 0
    assert "not an AAE backup" in (result.stderr + result.stdout)


# ── The round trip that matters ────────────────────────────────────────────

@pytest.mark.slow
def test_the_audit_chain_still_verifies_after_a_restore(
    archive, agent, viewer, reader_conn
) -> None:
    """Destroy governance state, restore, and ask REMORA whether it is intact.

    Deliberately asks the core, not this product: a restore that only proves
    our own reader is happy proves nothing about the chain.
    """
    before = viewer.verify_audit_chain()
    assert before.valid, "the chain was already broken before the test ran"
    records_before = before.records_checked

    # Destroy: drop the work-order rows the chain's entries refer to, so a
    # restore has something real to put back.
    subprocess.run(
        ["docker", "compose", "exec", "-T", "workorder-db",
         "psql", "-U", "wo_admin", "-d", "workorders", "-q", "-c",
         "DELETE FROM work_order_events; "
         "UPDATE work_orders SET status='open', updated_by='wiped'"],
        cwd=ROOT, capture_output=True, text=True, check=False)

    with reader_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM work_order_events")
        assert cur.fetchone()[0] == 0, "the destroy step did not take effect"

    result = _run("restore", str(archive))
    assert result.returncode == 0, result.stderr

    after = viewer.verify_audit_chain()
    assert after.valid, (
        f"the audit chain does not verify after a restore: {after.problems}")
    assert after.records_checked >= records_before, (
        "the restore lost chain records")


@pytest.mark.slow
def test_the_system_of_record_comes_back(archive, reader_conn) -> None:
    """The business data, not just the governance state."""
    _run("restore", str(archive))
    with reader_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM work_orders")
        assert cur.fetchone()[0] >= 4
        cur.execute("SELECT count(*) FROM work_orders WHERE updated_by='wiped'")
        assert cur.fetchone()[0] == 0, "restored rows still carry the wipe marker"
