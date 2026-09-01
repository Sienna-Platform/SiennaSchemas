# SiennaSchemas

[![Deploy Documentation](https://github.com/Sienna-Platform/SiennaSchemas/actions/workflows/jekyll-gh-pages.yml/badge.svg)](https://sienna-platform.github.io/SiennaSchemas/)
[![Validate Schemas](https://github.com/Sienna-Platform/SiennaSchemas/actions/workflows/validate-schemas.yml/badge.svg)](https://github.com/Sienna-Platform/SiennaSchemas/actions/workflows/validate-schemas.yml)

Machine-readable definitions of the things in an electric power system — buses, generators,
transmission lines, loads, storage, costs, and time series. They describe the exact shape of
each kind of data, so that tools written in different languages agree on it.

> [!WARNING]
> **Pre-release.** Every version below `1.0` is a pre-release. Schemas can change
> incompatibly in any release, and no stability is promised until `1.0`. Pin an exact
> tag if you depend on this.

## What you get from them

Typed Python classes, typed Julia structs, and a relational database schema are all generated
from these definitions. One definition, several languages, and no drift between them: when a
generator gains a field, every language gets it in the same release.

Every numeric value carries a unit. A field is MW or MVAr or ohms or seconds by declaration,
not by convention, and the generated database registry enforces it.

## You probably want a generated package, not this repository

This repository holds the definitions themselves. Unless you are changing them, use the
package built for your language:

| To work in | Use |
|---|---|
| Python | [power-openapi-models](https://github.com/Sienna-Platform/power-openapi-models) — Pydantic models |
| Julia | [PowerOpenAPIModels](https://github.com/Sienna-Platform/PowerOpenAPIModels) — type definitions |
| SQL | [SiennaGridDB](https://github.com/NREL-Sienna/SiennaGridDB) — SQLite DDL and the sealed unit registry |

There is no install step here — this repository is not a package. It publishes tagged
releases, and each of those repositories regenerates its own code from a tag.

## What is defined

183 types, in six groups. Each group is generated as its own package, so a project can take
only the parts it needs.

| Group | What it models | Package |
|---|---|---|
| **Grid topology and equipment** | Buses, areas and zones, lines and transformers, generators, loads, storage, and services such as reserves | PowerOperationsOpenAPIModels |
| **Costs and curves** | Cost and value curves, fuel and technology enumerations, and the other pieces the power groups share | PowerCoreOpenAPIModels |
| **Investment planning** | Candidate technologies, financial data, requirements, regions, and portfolios | PowerInvestmentsOpenAPIModels |
| **Machine dynamics** | Dynamic generator and inverter models | PowerDynamicsOpenAPIModels |
| **Time series** | Six ways of attaching a series of values to a component: single, non-sequential, deterministic, deterministic-single, probabilistic, and scenarios | InfrastructureTimeSeriesOpenAPIModels |
| **Shared basics** | Unit systems, function-data shapes, value pairs (`MinMax`, `UpDown`, `FromTo`, …), geographic information, and data provenance | InfrastructureCoreOpenAPIModels |

The shared basics group contains nothing power-specific, on purpose: software with no
power-system concepts in it can depend on that group alone. `scripts/check_layering.py`
enforces the boundary in CI, in both directions.

## Versioning

This repository is pre-1.0: any release can change schemas incompatibly, with no deprecation
window. Pin an exact tag — never track a moving branch or "latest". See
[`CHANGELOG.md`](CHANGELOG.md) for what changed in each release.

---

## Contributing

Start with `.claude/CLAUDE.md`. It documents the schema conventions, the unit-annotation
rules, the directory layout ($ref paths are relative and load-bearing — moving a file breaks
every reference to it), and the change protocol for editing a schema end to end. It ships in
the release tarball on purpose (a deliberate contributor resource, not internal tooling) —
don't strip it as stray build residue. A schemas-authoring skill under `.claude/skills/` may
follow later as a further contribution aid.

### Units

Every numeric property carries a unit annotation (`x-unit`, or `x-units` +
`x-unit-discriminator` for discriminated cases).

- **`Core/units.json`** — the single source of truth for allowed units and quantity types.
  It is consumed by the validator here and by SiennaGridDB's registry generator downstream.
- **`docs/UNIT_ANNOTATIONS.md`** — the annotation spec: rules for `x-unit`, the `pu`
  channel, discriminated units, and the physics conventions (reactive power is `MVAr`,
  impedance/admittance are `pu`, percent is banned in favor of fractions).
- **`docs/PIPELINE.md`** — the end-to-end pipeline: schemas → bundles → generated model
  packages, schemas → GridDB registry/DDL, and the PSY parity gate
  (`scripts/check_psy_parity.py`), with the change protocol and release order.

### Validating locally

Nine gates run across this repo, PowerOpenAPIModels, and SiennaGridDB — the full registry,
what each one catches, and the commands to run them, is `docs/PIPELINE.md`. The gates that
live in this repo:

```bash
python3 scripts/validate_units.py
python3 scripts/validate_units.py --check-descriptions
python3 scripts/bundle_specs.py --check
python3 scripts/check_psy_parity.py --psy-path ../PowerSystems.jl
python3 scripts/check_layering.py
python3 scripts/validate_fixtures.py
python3 scripts/check_infrastore_parity.py
```

Some gates import `jsonschema`, which the ambient `python3` may not have installed. This
repo carries a `.venv` with it — use `.venv/bin/python3` in place of `python3` above if the
ambient interpreter fails with an import error.

### Local codegen, for testing a schema change

Use `openapi-generator` with the provided config files:

```bash
# Julia
openapi-generator generate -c openapi-config-core.json \
  -g julia-server \
  -o ./PowerCoreOpenAPIModels.jl

# Python
openapi-generator generate -c openapi-config-core.json \
  -g python \
  -o ./power_core_openapi_models
```

Replace `core` with `infrastructure-core`, `operations`, `investments`, `dynamics`, or
`timeseries` for other packages. This is for testing a schema change locally; it is not how
the downstream packages are produced in production — each downstream repo has its own
codegen container and environment, documented in that repo. SiennaGridDB's own generation —
SQLite DDL and the sealed unit registry, both projected from these schemas — works the same
way: see SiennaGridDB's [Code generation](https://github.com/NREL-Sienna/SiennaGridDB#code-generation)
section.

### What a release contains

The tarball for a tagged release ships:

- the raw schema files (`Core/`, `Operations/`, `Investments/`, `Dynamics/`, `TimeSeries/`)
- `Core/units.json`, the unit vocabulary
- the built `dist/openapi-*-bundled.json` specs

Codegen consumes the **bundled** specs, not the raw `openapi-*.json` selector files.
Bundling inlines every `$ref`, including the sibling `x-unit`/`x-units` annotations that live
next to a `$ref` rather than inside the referenced definition — openapi-generator does not
resolve those siblings on an unbundled spec, so consuming the raw files silently drops unit
information. The bundled specs also carry a matching `Units:` sentence in every property
description, because openapi-generator does not render the `x-unit` vendor extension at all;
the description is the one channel every generator target preserves.

### Creating a release

```bash
git tag v0.1.0
git push origin v0.1.0
```

This triggers the release workflow, which builds the bundled specs and publishes the schema
tarball as a GitHub Release. Downstream repos pick up the new tag on their next polling cycle
or via manual workflow dispatch. SiennaSchemas tags first — see "Release order" in
`docs/PIPELINE.md` for why the other repos must not release ahead of it.

## License

SiennaSchemas is released under a BSD [license](https://github.com/Sienna-Platform/SiennaSchemas/blob/main/LICENSE). SiennaSchemas has been developed as part of the Sienna ecosystem as a collaboration between [QXT Energy](qxt.energy) the U.S. Department of Energy's National Laboratory of the Rockies [NLR](https://www.nlr.gov/) (formerly known as NREL) with support from OpenGrids, Breaktrough Energy and the U.S. Department of Energy.
