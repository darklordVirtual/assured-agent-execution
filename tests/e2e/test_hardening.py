# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Deployment hardening, asserted against the containers that are running.

Every property here was ABSENT until a survey of the live stack found it:
writable root filesystems, full default capabilities, no resource ceiling,
ports bound to every interface, and a Prometheus endpoint served without a
token. None of it is exotic — it is the list a security review works through
before it reads any application code — and all of it had been inherited from
an upstream pilot whose own comments said "local pilot only".

Asserted against `docker inspect` and live HTTP rather than against the
compose file, because what is running is the only thing that protects anyone.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.usefixtures("live")

_SERVICES = ("control-plane", "console", "control-plane-db", "workorder-db")


def _inspect(service: str) -> dict:
    if shutil.which("docker") is None:
        pytest.skip("docker is not on PATH")
    probe = subprocess.run(
        ["docker", "inspect", f"aae-{service}-1"],
        capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip(f"aae-{service}-1 is not running")
    return json.loads(probe.stdout)[0]


# ── Privileges ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("service", _SERVICES)
def test_every_container_drops_all_capabilities(service: str) -> None:
    """Default Docker capabilities include CHOWN, SETUID, NET_RAW and more.

    None of them is needed to serve HTTP or to answer SQL, and each is a rung
    an attacker who reaches code execution can climb.
    """
    host = _inspect(service)["HostConfig"]
    assert host["CapDrop"] == ["ALL"], (
        f"{service} keeps the default capability set")


@pytest.mark.parametrize("service", _SERVICES)
def test_every_container_refuses_privilege_escalation(service: str) -> None:
    """`no-new-privileges` makes setuid binaries inert.

    Without it, a writable setuid binary anywhere in the image is a path back
    to root regardless of which user the process starts as.
    """
    host = _inspect(service)["HostConfig"]
    assert "no-new-privileges:true" in (host["SecurityOpt"] or []), (
        f"{service} allows privilege escalation")


@pytest.mark.parametrize("service", ("control-plane", "console"))
def test_the_application_containers_run_read_only(service: str) -> None:
    """A writable root filesystem lets a compromise persist across a restart.

    The two databases legitimately write their data directories and are
    excluded; these two write nothing outside /tmp and a named volume.
    """
    assert _inspect(service)["HostConfig"]["ReadonlyRootfs"] is True, (
        f"{service} has a writable root filesystem")


@pytest.mark.parametrize("service", _SERVICES)
def test_no_container_runs_as_root(service: str) -> None:
    config = _inspect(service)["Config"]
    user = (config.get("User") or "").strip()
    # Postgres' entrypoint starts as root and drops to the postgres user; the
    # capability set above is what bounds it. The application images declare a
    # non-root user outright.
    if service.endswith("-db"):
        pytest.skip("postgres drops privileges in its own entrypoint")
    assert user and user != "root" and not user.startswith("0"), (
        f"{service} runs as {user or 'root'}")


# ── Resource ceilings ──────────────────────────────────────────────────────

@pytest.mark.parametrize("service", _SERVICES)
def test_every_container_has_a_memory_and_pid_ceiling(service: str) -> None:
    """Without either, one runaway process takes the host down with it —
    including the databases holding the audit chain."""
    host = _inspect(service)["HostConfig"]
    assert host["Memory"] > 0, f"{service} has no memory limit"
    assert host["PidsLimit"], f"{service} has no PID limit (fork bomb)"


# ── Network exposure ───────────────────────────────────────────────────────

@pytest.mark.parametrize("service", ("control-plane", "console", "workorder-db"))
def test_published_ports_bind_loopback_only(service: str) -> None:
    """0.0.0.0 puts the governance API and the work-order data on the LAN.

    On a laptop on a shared network that is every colleague, every guest, and
    anything else on the segment. These were bound to every interface until
    this test existed.
    """
    bindings = _inspect(service)["HostConfig"]["PortBindings"] or {}
    assert bindings, f"{service} publishes nothing (expected a host port)"
    for container_port, hosts in bindings.items():
        for binding in hosts:
            assert binding.get("HostIp") == "127.0.0.1", (
                f"{service} publishes {container_port} on "
                f"{binding.get('HostIp') or '0.0.0.0'} — reachable from the "
                f"network, not just this machine")


def test_the_governance_state_database_is_not_published() -> None:
    """Only the control plane needs it, so nothing else may reach it."""
    assert not (_inspect("control-plane-db")["HostConfig"]["PortBindings"] or {}), (
        "the governance state database is published to the host")


# ── What is served without a token ─────────────────────────────────────────

def test_prometheus_metrics_require_a_token(live) -> None:
    """Decision volumes and auth-failure counts are not public facts.

    `REMORA_PROMETHEUS_PUBLIC` arrived as "1", inherited from an upstream
    pilot that documented it as local-only. An unauthenticated scrape tells an
    attacker how much traffic this deployment governs, how often auth fails,
    and when a campaign is being noticed.
    """
    import httpx

    response = httpx.get(f"{live.api_url}/metrics", timeout=10)
    assert response.status_code == 401, (
        f"/metrics answered {response.status_code} without a token")


@pytest.mark.parametrize("path", ["/", "/v1/health"])
def test_the_navigation_endpoints_stay_open(live, path: str) -> None:
    """Liveness and navigation must NOT need a token.

    A health probe that requires a credential is a health probe that fails
    for the wrong reason during an incident. These disclose nothing a port
    scan does not.
    """
    import httpx

    response = httpx.get(f"{live.api_url}{path}", timeout=10)
    assert response.status_code == 200


def test_no_secret_reaches_the_console_api(live) -> None:
    """The console reports whether signing is configured, never the key.

    It holds bearer tokens because it is a local product install, and its
    posture panel is deliberately booleans — a console that echoed the key it
    reports on would be worse than one that reported nothing.
    """
    import os

    import httpx
    from aae.config import load_env_file

    load_env_file()
    posture = httpx.get(f"{live.api_url.replace('8088', '8089')}/api/posture",
                        timeout=10)
    if posture.status_code != 200:
        pytest.skip("console is not reachable on the derived port")

    body = posture.text
    for name in ("REMORA_TOOLSPEC_SIGNING_KEY", "REMORA_AUDIT_SIGNING_KEY",
                 "AAE_READER_PASSWORD", "AAE_TOKEN_AGENT"):
        secret = os.getenv(name, "")
        if secret:
            assert secret not in body, f"{name} leaked through /api/posture"
