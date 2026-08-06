# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""Every path the README promises has to exist, spelled the way it is written.

A dead link on a public front page is a small thing that reads as a large one:
it is the first evidence a visitor gets about whether the rest is maintained.

The case check is the reason this file exists. `docs/ARCHITECTURE.md` and
`docs/architecture.md` are the same file on Windows and macOS and two
different files on the Linux host GitHub serves from — so a link can resolve
perfectly on the machine that wrote it and 404 for every reader. That happened
here, to three links at once, and nothing noticed.

Runs on the filesystem only: no network, no Docker, part of the fast gate.
"""
from __future__ import annotations

import pathlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = sorted(ROOT.glob("*.md")) + sorted(ROOT.glob("docs/**/*.md"))

#: (source file, link target) for every relative link in the documentation.
LINKS = [
    (path.relative_to(ROOT).as_posix(), target)
    for path in DOCS
    for target in re.findall(r"\]\((?!https?://|mailto:|#)([^)]+)\)",
                             path.read_text(encoding="utf-8"))
]


def _exists_exactly(source: str, target: str) -> bool:
    """Resolve as a case-sensitive filesystem would.

    Deliberately does NOT call ``Path.resolve()``. On Windows and macOS that
    canonicalises a path to the real on-disk spelling, so a link written
    ``docs/ARCHITECTURE.md`` comes back as ``docs/architecture.md`` and every
    later check passes — which is how the first version of this test failed to
    catch the exact bug it was written for.

    Instead each written component is compared against the real directory
    listing, so the spelling in the document is what gets checked.
    """
    parts: list[str] = []
    # Start from the linking document's directory.
    parts.extend(pathlib.PurePosixPath(source).parts[:-1])

    for part in pathlib.PurePosixPath(target.split("#")[0]).parts:
        if part == ".":
            continue
        if part == "..":
            if not parts:
                return False  # escapes the repository
            parts.pop()
            continue
        parts.append(part)

    if not parts:
        return False

    current = ROOT
    for part in parts:
        if not current.is_dir():
            return False
        if part not in {entry.name for entry in current.iterdir()}:
            return False
        current = current / part
    return True


@pytest.mark.parametrize("source, target", LINKS,
                         ids=[f"{s}->{t}" for s, t in LINKS])
def test_the_link_target_exists(source: str, target: str) -> None:
    assert _exists_exactly(source, target), (
        f"{source} links to {target}, which does not exist with that exact "
        f"spelling. On a case-insensitive filesystem this can look fine "
        f"locally and 404 on GitHub.")


def test_the_readme_links_to_every_document() -> None:
    """A document nobody can reach from the front page is a document nobody
    reads. Either link it, or it does not need to exist."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    reachable = set(re.findall(r"\]\((docs/[^)]+)\)", readme))
    # A directory link covers the files inside it.
    directories = {r for r in reachable if r.endswith("/")}

    orphans = []
    for path in sorted(ROOT.glob("docs/**/*.md")):
        relative = path.relative_to(ROOT).as_posix()
        if relative in reachable:
            continue
        if any(relative.startswith(d) for d in directories):
            continue
        # Reachable one hop from a linked document is enough.
        if any(relative.rsplit("/", 1)[-1] in doc.read_text(encoding="utf-8")
               for doc in DOCS if doc != path):
            continue
        orphans.append(relative)

    assert not orphans, f"unreachable from the README: {orphans}"


def test_the_badge_row_points_at_real_workflows() -> None:
    """A CI badge for a workflow that does not exist renders as "no status"
    and reads as a broken build."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    workflows = re.findall(
        r"actions/workflows/([A-Za-z0-9._-]+)/badge\.svg", readme)
    assert workflows, "the README has no CI badge"
    for name in workflows:
        assert (ROOT / ".github" / "workflows" / name).is_file(), (
            f"the CI badge names {name}, which is not in .github/workflows")
