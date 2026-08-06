# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Which core release created this database, and does it still match the pin?

REMORA 0.10.0 has no versioned migrations. Its stores call
``CREATE TABLE IF NOT EXISTS`` when they are first constructed, which is
enough to stand a database up and not enough to move one forward: against a
volume that already has the tables, the statement is a no-op. A later core
release that adds a column gets none, and nothing says so — the application
starts, serves, and fails on the first query that needs the new column.

That gap belongs upstream and is filed as one; writing core DDL here would be
the local workaround this product exists to avoid. What *is* the product's own
business is the pin: AAE chooses which core release runs, so AAE can record
which one initialised the data it is now serving, and refuse to be quiet when
those two stop being the same release.

So this module owns exactly one table, in AAE's namespace, holding one row:
the release that first created the core schema in this volume. Nothing here
touches a ``remora_*`` table — not to create one, not to alter one.

The check is advisory by design. A mismatch does not mean the database is
broken; it means nobody has established that it is fine. It is reported at
``python run.py doctor`` and by the console's system-assurance surface, where
an operator can act on it, rather than crashing a container that would very
likely have worked.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

import psycopg

#: Written by this module, in the product's namespace. The name says who owns
#: it, so nobody mistakes it for part of the core schema.
PROVENANCE_TABLE = "aae_core_schema_provenance"

#: The core tables this product depends on. Their absence is the other half of
#: the check: a volume with provenance but no schema means the store never
#: initialised, which used to surface as an empty console with no explanation.
EXPECTED_CORE_TABLES = (
    "remora_control_plane_decision_versions",
    "remora_control_plane_reviews",
    "remora_control_plane_followups",
    "remora_control_plane_evidence",
)


@dataclasses.dataclass(frozen=True)
class SchemaState:
    """What was found. ``ok`` false always carries a populated ``concerns``."""

    ok: bool
    pinned_release: str
    recorded_release: str | None
    missing_tables: tuple[str, ...]
    concerns: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self) | {
            "missing_tables": list(self.missing_tables),
            "concerns": list(self.concerns),
        }


def _pinned_release(lock_path: pathlib.Path) -> tuple[str, str]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    return lock["release_tag"], lock["wheel"]["sha256"]


def check(dsn: str, lock_path: pathlib.Path) -> SchemaState:
    """Compare the pinned core release against the one that built this volume.

    Records the pin on a database that has the core schema but no provenance
    row yet — the first run after this check was introduced, and every fresh
    volume. Recording is a plain insert of the product's own row; it makes no
    claim about a schema it did not create.
    """
    release, wheel = _pinned_release(lock_path)
    concerns: list[str] = []

    with psycopg.connect(dsn, autocommit=True) as con:
        con.execute(f"""
            CREATE TABLE IF NOT EXISTS {PROVENANCE_TABLE} (
                id              INTEGER PRIMARY KEY DEFAULT 1,
                release_tag     TEXT NOT NULL,
                wheel_sha256    TEXT NOT NULL,
                recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT one_row CHECK (id = 1)
            )""")

        present = {
            row[0] for row in con.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
        missing = tuple(t for t in EXPECTED_CORE_TABLES if t not in present)

        row = con.execute(
            f"SELECT release_tag, wheel_sha256 FROM {PROVENANCE_TABLE} "
            "WHERE id = 1").fetchone()

        if row is None and not missing:
            # A database carrying the core schema, seen for the first time.
            con.execute(
                f"INSERT INTO {PROVENANCE_TABLE} (id, release_tag, "
                "wheel_sha256) VALUES (1, %s, %s) ON CONFLICT DO NOTHING",
                (release, wheel))
            row = (release, wheel)

    recorded = row[0] if row else None

    if missing:
        concerns.append(
            f"the core schema is incomplete: {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} absent. The core creates "
            "its tables when a store is first constructed, so this usually "
            "means the control plane has not served a request against this "
            "database yet.")
    if row and row[1] != wheel:
        concerns.append(
            f"this database was initialised by core release {recorded}, and "
            f"the pin now names {release}. REMORA 0.10.0 has no migrations: "
            "CREATE TABLE IF NOT EXISTS does not alter a table that already "
            "exists, so any schema change in the newer release is NOT present "
            "here. Either confirm the schema is unchanged between the two "
            "releases and update the provenance row, or start from a fresh "
            "volume (python run.py reset) and re-import what you need.")

    return SchemaState(
        ok=not concerns,
        pinned_release=release,
        recorded_release=recorded,
        missing_tables=missing,
        concerns=tuple(concerns),
    )


# The control-plane database is on an internal network with no route off the
# host, so nothing outside the compose project can reach it — including this
# CLI. The module is therefore copied into the control-plane image and run
# there by `python run.py doctor`, which is why it depends on nothing but the
# standard library and psycopg. Keep it that way.
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--lock", required=True, type=pathlib.Path)
    options = parser.parse_args()

    state = check(options.dsn, options.lock)
    print(json.dumps(state.as_dict()))
    sys.exit(0 if state.ok else 1)
