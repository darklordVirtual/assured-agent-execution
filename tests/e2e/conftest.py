# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Fixtures for tests that run against the real, running stack.

These talk to the deployed control plane over HTTP through the pinned SDK, and
to the system of record over Postgres on the reader credential. Nothing is
mocked: the point is to exercise the product an external developer installs,
not a rehearsal of it.

If the stack is not up, every test here SKIPS with an instruction rather than
failing. A red suite that only means "you did not run make up" trains people
to ignore red.
"""
from __future__ import annotations

import pytest

from aae.config import Config, ConfigError


@pytest.fixture(scope="session")
def cfg() -> Config:
    try:
        return Config.from_env()
    except ConfigError as exc:
        pytest.skip(f"not configured: {exc}")


@pytest.fixture(scope="session")
def live(cfg: Config) -> Config:
    """The stack, confirmed reachable, or a skip that says how to start it."""
    from remora.sdk import RemoraClient, RemoraUnavailableError

    try:
        with RemoraClient(cfg.api_url, cfg.token_viewer) as client:
            client._request("GET", "/v1/health")  # noqa: SLF001
    except RemoraUnavailableError:
        pytest.skip(f"no control plane at {cfg.api_url} — run `make up`")
    return cfg


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
        with psycopg.connect(live.reader_dsn, connect_timeout=5) as conn:
            conn.read_only = True
            yield conn
    except psycopg.Error as exc:
        pytest.skip(f"system of record unreachable: {exc}")
