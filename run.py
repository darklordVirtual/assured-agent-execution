#!/usr/bin/env python3
# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Task runner for Assured Agent Execution. No `make` required.

    python run.py up          verify the pin, generate secrets, sign, build,
                              migrate, start, and wait until healthy
    python run.py scenarios   run the four decisions against the running stack
    python run.py verify      pin + contract tests + end-to-end tests
    python run.py down        stop everything and remove the volumes

The Makefile delegates every target here, so there is exactly one
implementation. That is not tidiness: a shell version and a Python version
drift, and the first person to notice is a new developer following a
walkthrough that no longer matches what runs.

`make` is not installed on a default Windows machine, and "install make first"
is a poor first instruction for a product whose pitch is that it installs in
one command. This file needs only Python, which you already need.

Named run.py, not aae.py: a module at the repository root shadows the `aae`
package in src/, so `from aae.config import ...` inside a script resolved to
THIS file instead. A from-scratch install is what found it.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if platform.system() == "Windows"
                  else "bin/python")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    printable = " ".join(str(c) for c in cmd)
    print(f"\n$ {printable}", flush=True)
    result = subprocess.run([str(c) for c in cmd], cwd=ROOT, **kwargs)
    if result.returncode != 0 and not kwargs.get("check") is False:
        raise SystemExit(result.returncode)
    return result


def compose(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return run(["docker", "compose", *args], **kwargs)


def require(tool: str, why: str) -> None:
    if shutil.which(tool) is None:
        raise SystemExit(
            f"{tool!r} is not on PATH, and {why}.\n"
            f"See README.md 'Install' for the three prerequisites.")


# ── targets ────────────────────────────────────────────────────────────────

def t_pin(_args) -> None:
    """Download and hash-verify the pinned REMORA core artifacts."""
    require("gh", "it is how the pinned core release is fetched")
    run([sys.executable, "scripts/verify_core_pin.py", "--out", "dist"])


def t_env(_args) -> None:
    """Generate this installation's secrets (never overwrites)."""
    run([sys.executable, "scripts/bootstrap_env.py"])


def t_deps(_args) -> None:
    """Create the venv and install the SDK from the verified wheel."""
    if not VENV_PY.exists():
        run([sys.executable, "-m", "venv", str(VENV)])
    t_pin(None)
    wheels = sorted((ROOT / "dist").glob("remora-*.whl"))
    if not wheels:
        raise SystemExit("no verified wheel in dist/; the pin step did not run")
    run([str(VENV_PY), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([str(VENV_PY), "-m", "pip", "install", "--quiet", "pytest",
         "psycopg[binary]", f"{wheels[-1]}[sdk]"])
    run([str(VENV_PY), "-m", "pip", "install", "--quiet", "-e", "."])


def t_sign(_args) -> None:
    """Sign the ToolSpec bundle and pin its digest into .env."""
    _ensure_deps()
    run([str(VENV_PY), "scripts/sign_toolpack.py", "--pin-into", ".env"])


def t_check_sign(_args) -> None:
    """Verify the signed bundle without re-signing it."""
    _ensure_deps()
    run([str(VENV_PY), "scripts/sign_toolpack.py", "--check"])


def _ensure_deps() -> None:
    if not VENV_PY.exists():
        t_deps(None)


def t_build(_args) -> None:
    """Build both images from the verified wheel."""
    require("docker", "the product runs in containers")
    t_pin(None)
    compose("build")


def t_up(_args) -> None:
    """The whole thing: verify, generate, sign, build, migrate, start, wait."""
    require("docker", "the product runs in containers")
    t_env(None)
    t_deps(None)
    t_sign(None)
    compose("build")
    compose("up", "-d")

    print("\nwaiting for the control plane to report healthy...", flush=True)
    for attempt in range(90):
        probe = subprocess.run(
            ["docker", "compose", "ps", "--format", "{{.Health}}",
             "control-plane"],
            cwd=ROOT, capture_output=True, text=True)
        if probe.stdout.strip().splitlines()[:1] == ["healthy"]:
            break
        time.sleep(2)
    else:
        print("\ncontrol plane did not become healthy. Last 40 lines:")
        compose("logs", "--tail", "40", "control-plane", check=False)
        raise SystemExit(1)

    port = _env_value("AAE_API_PORT", "8088")
    console = _env_value("AAE_CONSOLE_PORT", "8089")
    print(f"""
  Assured Agent Execution is up.

    API       http://localhost:{port}
    Console   http://localhost:{console}

  next:  python run.py scenarios
""")


def _env_value(key: str, default: str) -> str:
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return os.getenv(key, default)


def t_down(_args) -> None:
    """Stop everything. Keeps the data."""
    compose("down", check=False)


def t_reset(_args) -> None:
    """Stop everything AND destroy the volumes.

    Split from `down` deliberately: `down -v` erases the audit chain, and
    a command whose name says `stop` should not destroy evidence.
    """
    compose("down", "-v", check=False)
    print("volumes removed: audit chain, envelopes and work orders are gone")


def t_logs(_args) -> None:
    compose("logs", "-f", "control-plane", check=False)


def t_ps(_args) -> None:
    compose("ps", "-a", check=False)


def t_compat(_args) -> None:
    """Pinned-core contract tests. No Docker needed."""
    _ensure_deps()
    run([str(VENV_PY), "-m", "pytest", "tests/compatibility", "-q"])


def t_e2e(_args) -> None:
    """End-to-end tests against the running stack. Must actually run.

    AAE_REQUIRE_LIVE turns the conftest's skips into failures. Without it
    this command exits 0 when the stack is down and every test skipped —
    reporting more verification than it performed, which is the one thing
    a verification command must never do.
    """
    _ensure_deps()
    import os

    environment = {**os.environ, "AAE_REQUIRE_LIVE": "1"}
    run([str(VENV_PY), "-m", "pytest", "tests/e2e", "-q"], env=environment)


def t_check(_args) -> None:
    """Static and contract checks. No Docker, no running stack."""
    t_compat(None)


def t_verify(_args) -> None:
    """Everything, against a stack that must be up."""
    t_compat(None)
    t_e2e(None)


def t_reseed(_args) -> None:
    """Return the reference work orders to their seeded state.

    Business data only. REMORA's audit chain is deliberately untouched:
    erasing the record of what was decided in order to make a demo repeatable
    would defeat the product being demonstrated.
    """
    password = _env_value("WORKORDER_DB_PASSWORD", "")
    if not password:
        raise SystemExit("no .env; run `python run.py up` first")
    # Piped on stdin, not `-f`: /migrations is mounted on the migrate
    # service, not on the database, and adding a mount just to reseed would
    # give the database container a path into the repository it has no other
    # reason to hold.
    sql = (ROOT / "db" / "workorders" / "reset_demo.sql").read_text("utf-8")
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "-e", f"PGPASSWORD={password}",
         "workorder-db", "psql", "-U", "wo_admin", "-d", "workorders",
         "-q", "-v", "ON_ERROR_STOP=1"],
        cwd=ROOT, input=sql, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise SystemExit(f"reseed failed: {result.stderr.strip()}")
    print("  reference work orders reset (audit chain untouched)")


def t_scenarios(args) -> None:
    """Run the four decisions against the running stack.

    Reseeds first, because the scenarios genuinely mutate the system of
    record — closing WO-1202 is the point — and a second run against the
    leftovers is a different run. `--no-reset` keeps the accumulated state.
    """
    _ensure_deps()
    if not getattr(args, "no_reset", False):
        t_reseed(None)
    cmd = [str(VENV_PY), "-m", "aae.cli", "scenarios"]
    if getattr(args, "evidence_out", None):
        cmd += ["--evidence-out", args.evidence_out]
    run(cmd)


def t_doctor(_args) -> None:
    """What is pinned, what is served, what is reachable."""
    _ensure_deps()
    run([str(VENV_PY), "-m", "aae.cli", "doctor"], check=False)


def t_backup(args) -> None:
    """Dump both databases and the signed bundle to a verifiable archive."""
    _ensure_deps()
    out = getattr(args, "out", None) or "./backups/latest"
    run([str(VENV_PY), "scripts/backup.py", "backup", "--out", out])


def t_restore(args) -> None:
    """Restore an archive, after verifying it is what its manifest says."""
    _ensure_deps()
    source = getattr(args, "source", None)
    if not source:
        raise SystemExit("restore needs a directory: "
                         "python run.py restore --source ./backups/...")
    run([str(VENV_PY), "scripts/backup.py", "restore", source])


def t_sbom(args) -> None:
    """Inventory what is actually inside the built images."""
    _ensure_deps()
    run([str(VENV_PY), "scripts/sbom.py", "--out",
         getattr(args, "out", None) or "./sbom"])


def t_clean(_args) -> None:
    """Remove the venv and downloaded artifacts. Keeps .env."""
    for path in (VENV, ROOT / "dist", ROOT / ".pytest_cache"):
        shutil.rmtree(path, ignore_errors=True)
    print("removed .venv, dist and .pytest_cache (.env kept)")


TARGETS = {
    "up": t_up, "down": t_down, "build": t_build, "pin": t_pin, "env": t_env,
    "deps": t_deps, "sign": t_sign, "check-sign": t_check_sign,
    "check": t_check, "verify": t_verify, "compat": t_compat, "e2e": t_e2e,
    "reseed": t_reseed,
    "reset": t_reset,
    "scenarios": t_scenarios, "doctor": t_doctor, "logs": t_logs, "ps": t_ps,
    "backup": t_backup, "restore": t_restore, "sbom": t_sbom,
    "clean": t_clean,
}


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python run.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", choices=sorted(TARGETS),
                        help="what to do")
    parser.add_argument("--evidence-out",
                        help="scenarios: also export evidence to this directory")
    parser.add_argument("--out", help="backup: where to write the archive")
    parser.add_argument("--source", help="restore: the archive to restore")
    parser.add_argument("--no-reset", action="store_true",
                        help="scenarios: keep the accumulated state")
    args = parser.parse_args()
    TARGETS[args.target](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
