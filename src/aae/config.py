# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Where this product's tools point, and which identity each one uses.

Five bearer identities, and the separation between them is a property the
product demonstrates rather than documents:

    operator          may propose and may execute. May NOT approve.
    reviewer          may approve routine holds. May NOT execute.
    domain_expert     may approve production writes. May NOT execute.
    senior_authority  may approve escalations. May NOT execute.
    viewer            may read. May do neither.

None of them is ``admin``. admin holds every capability including execute, so
an admin approver can approve its own proposal and then execute it — which is
precisely the separation this table exists to create.

Three approver roles rather than one because REMORA's escalation contract
decides which role a given decision requires: a priority change is not a
production close is not a purge. ``approver_for()`` reads the decision's
``required_role`` and returns the matching identity, so a decision that needs
an authority this deployment does not have fails loudly instead of being
approved by whoever happened to be configured.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"


class ConfigError(RuntimeError):
    """The product is not configured to reach anything."""


def load_env_file(path: Path | None = None) -> None:
    """Load .env into the environment without overwriting what is already set.

    Real environment wins, so a deployment that injects secrets properly is
    never silently overridden by a leftover development file.
    """
    target = path or ENV_FILE
    if not target.is_file():
        return
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Config:
    api_url: str
    token_agent: str
    token_viewer: str
    reader_dsn: str
    #: approver role name -> bearer token
    approver_tokens: dict[str, str]

    def approver_for(self, required_role: str | None) -> tuple[str, str]:
        """(role, token) for the identity a decision requires.

        ``None`` means the decision named no role — a routine hold — which
        the reviewer covers. An unrecognised role raises rather than falling
        back: silently approving with a weaker identity than the decision
        asked for is the failure this whole table exists to prevent.
        """
        role = (required_role or "reviewer").strip().lower()
        token = self.approver_tokens.get(role)
        if token is None:
            raise ConfigError(
                f"this decision requires the {role!r} authority, and no token "
                f"is configured for it. Configured: "
                f"{sorted(self.approver_tokens)}. Approving with a different "
                f"identity would defeat the escalation it asked for."
            )
        return role, token

    @classmethod
    def from_env(cls) -> "Config":
        load_env_file()
        port = os.getenv("AAE_API_PORT", "8080")
        required = ["AAE_TOKEN_AGENT", "AAE_TOKEN_REVIEWER",
                    "AAE_TOKEN_EXPERT", "AAE_TOKEN_SENIOR", "AAE_TOKEN_VIEWER"]
        # The reader password is only needed to BUILD a DSN. A deployment that
        # supplies the whole DSN — the console does, over the compose network —
        # has no reason to hold the password separately, and demanding it made
        # the console's scenario run fail on a credential it does not need.
        explicit_reader_dsn = os.getenv("AAE_WORKORDER_READER_DSN", "").strip()
        if not explicit_reader_dsn:
            required.append("AAE_READER_PASSWORD")
        missing = [name for name in required
                   if not os.getenv(name, "").strip()]
        if missing:
            raise ConfigError(
                f"missing configuration: {', '.join(missing)}. "
                f"Run `python run.py up` (or `python scripts/bootstrap_env.py`) to "
                f"generate this installation's secrets."
            )
        # The reader connects from the host, so it reaches the system of
        # record through the published port rather than the compose network.
        reader_dsn = explicit_reader_dsn or (
            f"postgresql://aae_reader:{os.environ['AAE_READER_PASSWORD']}"
            f"@127.0.0.1:{os.getenv('AAE_WORKORDER_DB_PORT', '55432')}/workorders"
        )
        return cls(
            api_url=os.getenv("AAE_API_URL", f"http://127.0.0.1:{port}"),
            token_agent=os.environ["AAE_TOKEN_AGENT"],
            token_viewer=os.environ["AAE_TOKEN_VIEWER"],
            approver_tokens={
                "reviewer": os.environ["AAE_TOKEN_REVIEWER"],
                "domain_expert": os.environ["AAE_TOKEN_EXPERT"],
                "senior_authority": os.environ["AAE_TOKEN_SENIOR"],
            },
            reader_dsn=reader_dsn,
        )
