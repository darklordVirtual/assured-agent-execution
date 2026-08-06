# Author: Stian Skogbrott
# SPDX-License-Identifier: BUSL-1.1
"""What is actually inside the images this product runs.

    python run.py sbom --out ./sbom

Produces, per image: a CycloneDX 1.5 software bill of materials for the
Python environment, the base image digest, the image digest, and a manifest
hashing all of it.

**What this covers, and what it does not.** It is an SBOM of the *Python*
dependency tree, read from installed package metadata inside the image, so it
lists what is actually there rather than what a requirements file claims. It
does NOT enumerate the Debian packages in the base layer — that needs a scanner
this product does not ship, and calling a partial inventory a complete one
would be worse than shipping none. The base image is recorded by digest
instead, so the OS layer is at least *identified* where it is not itemised.

Nor is this build provenance. Provenance is an attestation by the builder that
a given output came from a given input; this is an observation of a built
image, made afterwards, by whoever ran the command. Both are useful and they
are not the same claim.
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

IMAGES = {
    "control-plane": "aae-control-plane",
    "console": "aae-console",
}


def _docker(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=300, **kwargs)


def _image_facts(image: str) -> dict:
    """Digests that identify this image and the layer it was built on."""
    inspect = _docker("image", "inspect", image)
    if inspect.returncode != 0:
        raise SystemExit(f"image {image} is not built; run `python run.py build`")
    data = json.loads(inspect.stdout)[0]
    return {
        "image_id": data.get("Id"),
        "repo_digests": data.get("RepoDigests") or [],
        "created": data.get("Created"),
        "base_image": (data.get("Config", {}).get("Labels") or {}).get(
            "org.opencontainers.image.base.name"),
        "os_layer_note": (
            "Debian packages in the base layer are NOT itemised here — that "
            "needs a scanner this product does not ship. The base image is "
            "identified by digest instead."),
    }


def _python_sbom(service: str) -> dict:
    """CycloneDX for the Python environment, read out of the running image.

    Reads installed package METADATA and nothing else. An earlier version
    pip-installed a generator inside the container and failed — because the
    container runs with a read-only root filesystem, which is the hardening
    working exactly as intended. An inventory tool that needs to modify the
    thing it inventories is the wrong tool.
    """
    result = subprocess.run(
        ["docker", "compose", "run", "--rm", "--no-deps",
         "--entrypoint", "python", service, "-c",
         "import json,importlib.metadata as m;"
         "print(json.dumps(sorted(("
         "  {'name': d.metadata['Name'], 'version': d.version}"
         "  for d in m.distributions() if d.metadata['Name']"
         "), key=lambda c: c['name'].lower())))"],
        cwd=ROOT, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise SystemExit(
            f"could not inventory {service}: {result.stderr.strip()[:400]}")
    start = result.stdout.find("[")
    if start < 0:
        raise SystemExit(f"no package list came back for {service}")
    packages = json.loads(result.stdout[start:result.stdout.rindex("]") + 1])

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "tools": [{"name": "aae/scripts/sbom.py", "version": "1"}],
            "component": {"type": "container", "name": IMAGES[service]},
        },
        "components": [
            {
                "type": "library",
                "name": pkg["name"],
                "version": pkg["version"],
                "purl": f"pkg:pypi/{pkg['name'].lower()}@{pkg['version']}",
            }
            for pkg in packages
        ],
    }


def generate(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    components = 0

    for service, image in IMAGES.items():
        print(f"  inventorying {service}")
        document = _python_sbom(service)
        document["metadata"] = {**document.get("metadata", {}),
                                "image": _image_facts(image)}
        count = len(document.get("components", []))
        components += count
        print(f"    {count} Python component(s)")

        path = out_dir / f"{service}.cdx.json"
        body = json.dumps(document, indent=2, sort_keys=True).encode("utf-8")
        path.write_bytes(body)
        files[path.name] = hashlib.sha256(body).hexdigest()

    lock = json.loads(
        (ROOT / "product" / "core-artifact-lock.json").read_text("utf-8"))
    manifest = {
        "manifest_version": 1,
        "product": "assured-agent-execution",
        "generated_at": datetime.now(UTC).isoformat(),
        "format": "CycloneDX 1.5 (Python environment only)",
        "core_release": lock["release_tag"],
        "core_wheel_sha256": lock["wheel"]["sha256"],
        "python_components": components,
        "files": {n: f"sha256:{d}" for n, d in sorted(files.items())},
        "covers": "the Python dependency tree installed in each image",
        "does_not_cover": (
            "Debian packages in the base layer, and build provenance. This is "
            "an observation of a built image, not an attestation by its "
            "builder that it came from a given input."),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _utf8() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from aae._io import force_utf8_output

    force_utf8_output()


def main() -> int:
    _utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="./sbom")
    args = parser.parse_args()

    manifest = generate(Path(args.out))
    print(f"\n  {manifest['python_components']} Python component(s) across "
          f"{len(manifest['files'])} image(s) → {args.out}")
    print(f"  covers: {manifest['covers']}")
    print(f"  does not cover: {manifest['does_not_cover']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
