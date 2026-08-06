# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Fixtures for tests that run against the real, running stack.

``AAE_REQUIRE_LIVE=1`` turns every skip below into a failure. ``run.py
verify`` sets it, because a command that reports success after skipping
every test claims more verification than it performed. Without the
variable the skips stand, so a contributor running pytest directly with
no stack up still gets a readable result rather than a wall of red.


These talk to the deployed control plane over HTTP through the pinned SDK, and
to the system of record over Postgres on the reader credential. Nothing is
mocked: the point is to exercise the product an external developer installs,
not a rehearsal of it.

If the stack is not up, every test here SKIPS with an instruction rather than
failing. A red suite that only means "you did not run run.py up" trains people
to ignore red.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aae.config import Config, ConfigError


def _unavailable(reason: str) -> None:
    """Skip, or fail when the caller demanded a live run."""
    import os

    if os.getenv("AAE_REQUIRE_LIVE", "").strip() in {"1", "true", "yes"}:
        pytest.fail(f"AAE_REQUIRE_LIVE is set and {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def cfg() -> Config:
    try:
        return Config.from_env()
    except ConfigError as exc:
        _unavailable(f"not configured: {exc}")


@pytest.fixture(scope="session")
def live(cfg: Config) -> Config:
    """The stack, confirmed reachable, or a skip that says how to start it."""
    from remora.sdk import RemoraClient, RemoraUnavailableError

    try:
        with RemoraClient(cfg.api_url, cfg.token_viewer) as client:
            client._request("GET", "/v1/health")  # noqa: SLF001
    except RemoraUnavailableError:
        _unavailable(f"no control plane at {cfg.api_url} — run `python run.py up`")
    return cfg


@pytest.fixture()
def seeded(live: Config):
    """Reference work orders back to their seeded state before the test.

    The scenarios genuinely mutate the system of record — closing WO-1202 is
    the point — so a test that runs second sees a different world than one
    that runs first. Business data only: REMORA's audit chain is deliberately
    untouched, because erasing what was decided to make a test repeatable
    would defeat the thing being tested.
    """
    import subprocess

    sql = (Path(__file__).resolve().parents[2] / "db" / "workorders"
           / "reset_demo.sql").read_text(encoding="utf-8")
    import os

    password = os.getenv("WORKORDER_DB_PASSWORD", "")
    if not password:
        pytest.skip("no WORKORDER_DB_PASSWORD; cannot reseed")
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "-e", f"PGPASSWORD={password}",
         "workorder-db", "psql", "-U", "wo_admin", "-d", "workorders",
         "-q", "-v", "ON_ERROR_STOP=1"],
        cwd=Path(__file__).resolve().parents[2], input=sql,
        capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        pytest.skip(f"reseed failed: {result.stderr.strip()[:200]}")
    return live


@pytest.fixture()
def agent(live: Config):
    from remora.sdk import RemoraClient

    with RemoraClient(live.api_url, live.token_agent) as client:
        yield client


@pytest.fixture()
def approver_for(live: Config):
    """Build a client holding exactly the authority a decision requires.

    Not a single fixed approver: REMORA decides per decision which role may
    release it, and a test that always used one credential would pass on a
    deployment where escalation had stopped meaning anything.
    """
    from contextlib import contextmanager

    from remora.sdk import RemoraClient

    @contextmanager
    def _for(required_role=None):
        role, token = live.approver_for(required_role)
        with RemoraClient(live.api_url, token) as client:
            yield role, client

    return _for


@pytest.fixture()
def viewer(live: Config):
    from remora.sdk import RemoraClient

    with RemoraClient(live.api_url, live.token_viewer) as client:
        yield client


@pytest.fixture()
def reader_conn(live: Config):
    """A connection on the read-only credential, as the verifier holds it."""
    import psycopg

    try:
        with psycopg.connect(live.reader_dsn, connect_timeout=5,
                             autocommit=True) as conn:
            conn.read_only = True
            yield conn
    except psycopg.Error as exc:
        _unavailable(f"system of record unreachable: {exc}")
