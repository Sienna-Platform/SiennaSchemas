# SiennaSchemas — Claude Guide

The **single source of truth for Sienna data-model schemas**: hand-written JSON Schema (draft-07) definitions organized by domain, the OpenAPI specs that select what each generated package contains, the **unit vocabulary** (`Core/units.json`), and the units validator. Everything here is source; the generated side lives downstream. Platform conventions: `.claude/Sienna.md`; workspace architecture: the psy6 workspace root `CLAUDE.md`.

Current branch: `jm/units` (units-annotation effort, PR #15). Master plan: the psy6 workspace root's `.claude/plans/2026-07-05-units-ecosystem-closure.md`.

## Multi-repository pipeline

```
SiennaSchemas (this repo — hand-written source)
  │  git tag vX.Y.Z → GH Actions release tarball; downstream polls; .schema-version records consumed tag
  ├─ openapi-*.json ──datamodel-codegen──▶ power-openapi-models   (Python / pydantic v2)
  ├─ openapi-*.json ──openapi-generator──▶ PowerOpenAPIModels     (5 Julia packages)
  └─ Core/units.json ──SiennaGridDB/scripts/generate_unit_registry.py──▶ GridDB sealed unit registry
```

- Schemas mirror **PowerSystems.jl component types field-for-field** but in natural units with integer-id references (PSY internals are per-unit with object references). A PSY field change without a schema change is drift — the C1 sync tooling (schemas ↔ GridDB registry ↔ PSY descriptor) exists to catch it. Live example of drift: `Operations/StaticInjection/HydroReservoir.json` still `$ref`s `ValueCurve` for `head_to_volume_factor` while PSY psy6 (commit `ed30a682`) moved it to `FunctionData` — this is the regression fixture for the sync check.
- **The PSY6 System ⇄ OpenAPI ⇄ GridDB serialize/deserialize loop is not closed yet** — no converter exists in either direction; that bridge is the next stage. Until then, validators are the only consistency mechanism; treat annotation/vocabulary correctness as load-bearing.

## Generated package membership

Four packages (each generated in both Julia and Python):

| Package | Contents | Dependencies |
|---|---|---|
| **PowerCoreOpenAPIModels** | Shared types: curves, enums, helpers (MinMax, UpDown, CostCurve, FunctionData, …) | None |
| **PowerOperationsOpenAPIModels** | Topology, Branch, StaticInjection, Service | Core |
| **PowerInvestmentsOpenAPIModels** | Technologies, Financials, Requirements, Attributes, Regions, Portfolio | Core |
| **PowerDynamicsOpenAPIModels** | DynamicGeneratorComponent, DynamicInverterComponent | Core |

Investments depends only on Core (not Operations); integer ID cross-references are semantic, not formal type dependencies.

## Directory structure

```
Core/                    # common.json (shared types), units.json (unit vocabulary),
                         # TimeSeries/, SupplementalAttributes/
Operations/              # Topology/, Branch/, StaticInjection/, Service/, SupplementalAttributes/
Investments/             # Technologies/, Financials/, Requirements/, Attributes/, Regions/, Portfolio/
Dynamics/                # DynamicGeneratorComponent/, DynamicInverterComponent/
openapi-{core,operations,investments,dynamics}.json     # $ref wrappers selecting package membership
openapi-config-*.json    # generator configs (inlineSchemaNameMappings)
scripts/validate_units.py   # x-unit annotation validator
scripts/bundle_specs.py     # → dist/openapi-*-bundled.json (units ride as Units: sentences)
dist/                       # bundled specs for codegen consumers
docs/UNIT_ANNOTATIONS.md
.github/workflows/          # release.yml, validate-schemas.yml
```

Cross-references are relative paths (`"$ref": "../../Core/common.json#/definitions/MinMax"`) — **directory layout is load-bearing**; moving a file breaks every `$ref` to it.

## Units annotations (the active effort)

- `Core/units.json` is the unit vocabulary — single source of truth, consumed by the validator here and by SiennaGridDB's registry generator (sibling-checkout relative path `../SiennaSchemas`).
- Every numeric property carries `x-unit` (or `x-units` + `x-unit-discriminator` for discriminated cases; `x-unit-base` names a sibling property). Values must be in `units.json` `allowed_units` or the literal `"pu"`. For the four branch-impedance quantities (Resistance/Reactance/Susceptance/Conductance), `pu` **is** a registered vocabulary unit — it is one of two discriminated storage options (per-row basis recorded by GridDB `transmission_lines.parameter_units`); elsewhere `"pu"` is just an annotation channel.
- Vocabulary rules: reactive power is `MVAr` (not MW — 63 fields across 27 files are being corrected); impedance/admittance annotations move from `ohm`/`S` to `"pu"`; percent is banned (store fractions).
- Validate: `python3 scripts/validate_units.py` (spec in `docs/UNIT_ANNOTATIONS.md`). Use `python3`, never `python`; the venv is `.venv-units` at the psy6 workspace root.
- Remaining annotation debt (post-PR #15): Dynamics numerics (deferred by design) and the ~56 documented sync-check WARNs (`SiennaGridDB/scripts/check_units_sync.py`) — gaps, not contradictions. Operations and Investments are annotated; `base_power` defaults are gone.

## Schema conventions

- JSON Schema draft-07 (`"$schema": "http://json-schema.org/draft-07/schema#"`).
- Discriminated unions: `oneOf` + a `discriminator` block (`propertyName` + `mapping`).
- Component base properties: `id` (integer), `name` (string), `available` (boolean).
- `ext`, supplemental attributes, and many-to-many relations are stored *separately*; one-to-many relations become integer id references.
- Natural units throughout, with the deliberate exceptions of power factor, cost curves, and branch electrical parameters (r/x/b/g), which are annotated `x-unit: "pu"` (system base) and stored downstream in per-unit or natural units via a per-row discriminator.
- Avoid read-only fields (e.g. for dynamic components).
- Property ordering follows Sienna conventions: id, name, bus, …
- Path-aliasing problems are resolved via `inlineSchemaNameMappings` in `openapi-config-*.json` (workaround for [openapi-generator #18948](https://github.com/OpenAPITools/openapi-generator/issues/18948)).

## Generator configs & local codegen check

```bash
openapi-generator generate -c openapi-config-core.json -g julia-server -o ./PowerCoreOpenAPIModels.jl
```

Config files are language-agnostic; pick `-g python` or `-g julia-server` on the command line. (The production Julia generation actually happens in the PowerOpenAPIModels repo via `julia-client` + its `reorganize.jl`; the Python side uses datamodel-codegen, not openapi-generator — bug classes differ between the two toolchains.)

## Recipe: change a schema (end-to-end)

1. Edit the domain JSON (respect conventions below); keep `$ref` paths valid — layout is load-bearing.
2. Annotate numerics with `x-unit` from `Core/units.json`; run `python3 scripts/validate_units.py`.
3. Rebundle: `python3 scripts/bundle_specs.py` → `dist/` (units ride as `Units:` description sentences for generators that drop vendor extensions).
4. If the change mirrors a PSY descriptor change, confirm both sides (drift check); if it affects DB columns, coordinate `SiennaGridDB/schema/column_conventions.json` + registry regeneration — **Schemas release before GridDB regenerates**.
5. Regenerate and validate both model packages (`power-openapi-models`: `make generate && make validate`; `PowerOpenAPIModels`: `make generate && make validate`) — codegen breakage is cheapest to catch here.
6. Tag a release when the change set is coherent (see below).

## Creating a release

```bash
git tag v1.0.0
git push origin v1.0.0
```

The tag push triggers the GH Actions release workflow (schema tarball → GitHub Release). Downstream repos poll for releases and record the consumed tag in their `.schema-version`. Note: both downstream `.schema-version` files currently read `none` — the release-driven regeneration loop has never fired; current generated models came from ad-hoc sibling-checkout generation.

## Units effort — where the docs live

The units annotation work is documented for humans in `README.md` (Units section) and
`docs/UNIT_ANNOTATIONS.md` (the annotation spec). The vocabulary is `Core/units.json`;
validate with `scripts/validate_units.py`; bundle for codegen with `scripts/bundle_specs.py`
(units also ride to consumers as `Units:` sentences in bundled descriptions, since
openapi-generator drops the `x-unit` vendor extension). The downstream registry lives in
SiennaGridDB (`scripts/generate_unit_registry.py --units-json ../SiennaSchemas/Core/units.json`),
kept in lockstep by its `scripts/check_units_sync.py`. Merge order is load-bearing: tag a
SiennaSchemas release (with `units.json` + `dist/` bundles) **before** GridDB regenerates.

## Branch/merge state (2026-07 snapshot)

`origin/main` is merged into `jm/units` (merge commit `dff9f40`; the 4 Dynamics-controller conflicts are resolved). `main`'s `Core/TimeSeries/TimeSeriesAssociation.json` `units` property now carries a vocabulary-pointing description; `GeographicInfo.json` arrived unchanged. Keep this branch discipline: Schemas on `jm/units`, SiennaGridDB on `jm/units_v2`, IS on `IS4`, PSY on `psy6`.
