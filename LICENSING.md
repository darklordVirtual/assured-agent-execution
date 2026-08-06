# Assured Agent Execution — Licensing

## Current licensing

Assured Agent Execution (AAE) versions beginning with 0.1.0 are available
under either:

1. the Business Source License 1.1 included in [`LICENSE`](LICENSE); or
2. a separate written AAE Commercial License issued by the Licensor.

The Licensor is **Stian Skogbrott**. No one may use AAE commercially
without a commercial license from, and compensation agreed with, the
Licensor.

You may choose the license that applies to your use, provided that you
meet all of its conditions.

## Two licensed works, not one

This repository is a **product** that consumes a pinned **REMORA core**
release. They are separate Licensed Works:

| | Licensed Work | License text |
|---|---|---|
| This repository | Assured Agent Execution 0.1.0+ | [`LICENSE`](LICENSE) |
| The pinned core artifacts (`product/core-artifact-lock.json`) | REMORA 0.10.0 | shipped inside the REMORA wheel and with its release |

An AAE license grants nothing in REMORA, and a REMORA license grants
nothing in AAE. A deployment that runs this product runs both, and needs
its use to be permitted under both. Both are licensed by the same
Licensor, so a single commercial agreement can cover both — but only if
it says so.

## What BSL permits

- inspection and study of the source code;
- copying and modification;
- creation and redistribution of derivative works under the BSL;
- development, testing, evaluation, demonstration and staging;
- the limited Production Uses listed in the Additional Use Grant.

Running the Docker profile in this repository to evaluate AAE — including
inside a for-profit organization — is non-production use and needs no
permission. Pointing it at a system that makes real operational decisions
is Production Use.

## Evaluation without a commercial license

Two grants exist specifically so evaluation does not require a
negotiation first:

- **Research Use** (Additional Use Grant ¶5) is granted to any person or
  organization: study, evaluate, benchmark, attempt to falsify, and
  publish what you find. University accreditation is not required.
- **Shadow-Mode Research Evaluation** (¶6) is granted to any
  organization, including commercial ones: run AAE observer-only —
  decisions recorded, never enforced, controlling no live system — for
  up to 90 consecutive days.

Publication is never restricted. Publishing measurements or comparisons,
favorable or not, requires no permission.

If your intended evaluation does not clearly fit these, write to
support@luftfiber.no and describe it in two sentences.

## When a commercial license is required

An AAE Commercial License is required for uses including:

- internal production use by a commercial organization;
- governing agent actions in revenue-generating or business-critical
  operations;
- use on behalf of a commercial customer;
- paid consulting engagements;
- embedding AAE in a product or platform;
- OEM, white-label or commercial redistribution;
- offering AAE functionality as SaaS, an API, a hosted service, managed
  service or service bureau;
- commercial sublicensing or resale.

This list is illustrative. The controlling terms are the Business Source
License and any signed commercial agreement. See
[`COMMERCIAL_LICENSE.md`](COMMERCIAL_LICENSE.md).

## Third-party materials

Third-party software and materials remain subject to their original
licenses. The dependency set is whatever the pinned REMORA wheel and the
container base images bring in; AAE declares no third-party vendored
source of its own.

## Trademarks

These licenses grant no rights to use the "Assured Agent Execution",
"AAE" or "REMORA" names or logos as branding for another product.

## Contact

support@luftfiber.no
