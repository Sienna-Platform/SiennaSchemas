# The Sienna Data Pipeline: Single Source of Truth

The JSON Schemas in this repository, together with the unit vocabulary in
`Core/units.json`, define every component and every unit exactly once. The
Julia and Python model packages and the SiennaGridDB SQLite registry and DDL
are generated from the schemas. PowerSystems.jl is a separate, external data
model and is not part of this repository's generation pipeline.

```
Core/units.json ──► JSON Schemas (x-unit annotated)
                        │                │
                        │      openapi-*.json selectors
                        │                ├── OpenAPI.jl native generator ──► Julia model package (PowerOpenAPIModels)
                        │                └── datamodel-codegen ──► Python model package (power-openapi-models)
                        ▼
                  SiennaGridDB
                         ├── scripts/generate_unit_registry.py  (vocabulary → sealed registry)
                         ├── scripts/generate_sql_schema.py     (schemas → DDL reference, --diff gate)
                         └── scripts/check_units_sync.py        (three-layer unit sync gate)
```

## The ten gates

| Gate | Repo | Command | Prevents |
|---|---|---|---|
| Unit annotations | SiennaSchemas | `python3 scripts/validate_units.py` | annotations outside the `Core/units.json` vocabulary; malformed `x-unit-base`/`x-units` |
| Description channel | SiennaSchemas | `python3 scripts/validate_units.py --check-descriptions` | silent unit loss in generated code (descriptions are the channel that survives codegen; neither toolchain renders the `x-unit` vendor extension) |
| Reference resolution and selector completeness | SiennaSchemas | `python3 scripts/check_refs.py` | a `$ref` or `discriminator.mapping` value that names nothing; and a domain that reaches a schema its selector does not declare, which leaves the generators no name for it so each invents one per reference site and a shared type silently becomes several; and a `default` whose JSON type differs from the declared `type`, which the generators emit as a wrong-typed literal; and a `required` name not defined in `properties`, which the generators turn into a field with no schema |
| Layering | SiennaSchemas | `python3 scripts/check_layering.py` | power semantics leaking into InfrastructureCore: an undeclared or missing member, a schema name shared with the power core, or a `$ref` chain from the InfrastructureCore/TimeSeries selectors reaching a file or definition outside the InfrastructureCore set |
| Fixtures, versioning vectors, strict bundles | SiennaSchemas | `python3 scripts/validate_fixtures.py` | a broken `oneOf` discriminator, a wrong `required` list, or a discriminator `mapping` naming the wrong schema, exercised against real example instances rather than structure alone; also the reader-rule vectors and error texts, and the strict bundles' accept/reject checks |
| Infrastore parity | SiennaSchemas | `python3 scripts/check_infrastore_parity.py` | field-name drift between the six time series schemas and infrastore's `time_series_associations` catalog row; SKIPs cleanly when no infrastore checkout is present, so a green run there is not proof the check ran |
| Version compatibility | SiennaSchemas | `python3 scripts/check_compat.py` | a version below what the schema diff since the last tag requires: a breaking change needs a new line (minor in 0.x, major from 1.0), a feature needs the next patch in 0.x or the next minor from 1.0. Report-only while `info.version` still equals the base tag; fails closed on any keyword it does not classify |
| Model build | SiennaSchemas CI (`build-models.yml`) | `scripts/stage_release.sh`, then each model repository's own generate targets and a build: precompile for Julia; import, `tsc --noEmit` and `cargo build` for Python, TypeScript and Rust | a schema change that breaks a generator or leaves generated code that does not compile, caught on the schema PR rather than on the downstream schema-update PR. It builds against each model repository's `main` and does not run their test suites, which change with downstream fixtures |
| Inline schema aliases | PowerOpenAPIModels | `make validate` (`test/validate.jl`) | a shared schema silently duplicated as `<Base>1`, `<Base>2`, … when an inline object at the reference site has no named `$defs` entry. This is the downstream backstop: alias names are assigned by OpenAPI.jl's native generator and cannot be derived statically from the schemas alone, so a check here would have false negatives. Keys on the unsuffixed base existing, so real digit-suffixed type names (`SteamTurbineGov1`) are not flagged |
| DB sync | SiennaGridDB | `python3 scripts/check_units_sync.py` and `python3 scripts/generate_sql_schema.py --check --diff` | unit contradictions between registry and schemas; DDL drifting from the schema projection |

## Change protocol

1. **Component change owned by PSY** (new struct, field, enum): change the PSY
   descriptor first, then mirror it here — same property names (ASCII
   transliteration for Unicode field names), `x-unit` on every numeric
   property, enums as string enums in `Core/common.json`. Run the gate
   battery.
2. **New unit or quantity type**: edit `Core/units.json` only, then regenerate
   the SiennaGridDB registry (`scripts/generate_unit_registry.py`). Nothing
   else hand-maintains vocabulary.
3. **DB-owned mapping change** (column rename, new table mapping): edit
   SiennaGridDB's `schema/schema_map.json` / `schema/sql_codegen_map.json` /
   `schema/column_conventions.json`, regenerate, re-run its suite.
4. **Never hand-edit generated files**: `SiennaGridDB/schema/unit_registry.sql`,
   `SiennaGridDB/schema/generated_schema.sql`, and the downstream model packages.

Deliberate differences between PSY and the schemas: infra fields (`internal`, `ext`, `services`, container
fields) are dropped; schemas add integer `id`s; `Reserve{T}` direction is
flattened to a `reserve_direction` property; PSY relationship/map fields are
normalized into association components (`PlantAssociation`,
`CombinedCycleAssociation`); `Source.base_voltage` is schemas-ahead-of-PSY.

## Release order

SiennaSchemas tags first — the release tarball must include `Core/units.json`
and the `openapi-*.json` selectors. SiennaGridDB and PowerOpenAPIModels then consume the
tag. Merging or releasing in the other order leaves consumers generating from
vocabulary or components that do not exist yet.

## Known deferred items

- Dynamics component schemas.
- Neither codegen toolchain renders the `x-unit` vendor extension;
  the `Units:` description sentences carry units to generated code meanwhile.
- SiennaGridDB tables for `GenericArcImpedance`, `TransmissionInterface`, and
  `HybridSystem` (schemas exist; no DB tables yet).
