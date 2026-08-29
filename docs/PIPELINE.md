# The Sienna Data Pipeline: Single Source of Truth

The JSON Schemas in this repository, together with the unit vocabulary in
`Core/units.json`, define every component and every unit exactly once. The
Julia and Python model packages and the SiennaGridDB SQLite registry and DDL
are generated from the schemas. PowerSystems.jl is a separate, external data
model that is gate-checked against these schemas for field parity, but is not
itself part of this repository's generation pipeline.

```
Core/units.json ──► JSON Schemas (x-unit annotated)
                                 │
                scripts/bundle_specs.py ──► dist/openapi-*-bundled.json
                                 │                     │
                                 │           openapi-generator (PowerOpenAPIModels)
                                 │              ├── Julia model package
                                 │              └── Python model package
                                 ▼
                       SiennaGridDB
                         ├── scripts/generate_unit_registry.py  (vocabulary → sealed registry)
                         ├── scripts/generate_sql_schema.py     (schemas → DDL reference, --diff gate)
                         └── scripts/check_units_sync.py        (three-layer unit sync gate)
```

## The eight gates

| Gate | Repo | Command | Prevents |
|---|---|---|---|
| Unit annotations | SiennaSchemas | `python3 scripts/validate_units.py` | annotations outside the `Core/units.json` vocabulary; malformed `x-unit-base`/`x-units` |
| Description channel | SiennaSchemas | `python3 scripts/validate_units.py --check-descriptions` | silent unit loss in generated code (descriptions are the channel openapi-generator renders) |
| Bundle staleness | SiennaSchemas | `python3 scripts/bundle_specs.py --check` | codegen consuming stale `dist/` artifacts that drop `$ref`-sibling annotations |
| PSY parity | SiennaSchemas | `python3 scripts/check_psy_parity.py --psy-path ../PowerSystems.jl` | structural drift between PowerSystems.jl structs and schema components (missing schemas, field drift); SKIPs cleanly when PSY is absent |
| Time series fixtures | SiennaSchemas | `python3 scripts/validate_fixtures.py` | a broken `oneOf` discriminator, a wrong `required` list, or a discriminator `mapping` naming the wrong schema, exercised against real example instances rather than structure alone |
| Infrastore parity | SiennaSchemas | `python3 scripts/check_infrastore_parity.py` | field-name drift between the six time series schemas and infrastore's `time_series_associations` catalog row; SKIPs cleanly when no infrastore checkout is present, so a green run there is not proof the check ran — mirroring how PSY parity SKIPs when PSY is absent |
| Inline schema aliases | PowerOpenAPIModels | `make validate` (`test/validate.jl`) | a shared schema silently duplicated as `<Base>1`, `<Base>2`, … because the reference site had no `inlineSchemaNameMappings` entry in this repo's `openapi-config-*.json`. Lives downstream because the evidence is the generated output: the alias names are assigned by openapi-generator and cannot be derived statically from the schemas, so a check here would have false negatives. Keys on the unsuffixed base existing, so real digit-suffixed type names (`SteamTurbineGov1`) are not flagged |
| DB sync | SiennaGridDB | `python3 scripts/check_units_sync.py` and `python3 scripts/generate_sql_schema.py --check --diff` | unit contradictions between registry and schemas; DDL drifting from the schema projection |

## Change protocol

1. **Component change owned by PSY** (new struct, field, enum): change the PSY
   descriptor first, then mirror it here — same property names (ASCII
   transliteration for Unicode field names), `x-unit` on every numeric
   property, enums as string enums in `Core/common.json`. Run the gate
   battery; `check_psy_parity.py` confirms closure.
2. **New unit or quantity type**: edit `Core/units.json` only, then regenerate
   the SiennaGridDB registry (`scripts/generate_unit_registry.py`). Nothing
   else hand-maintains vocabulary.
3. **DB-owned mapping change** (column rename, new table mapping): edit
   SiennaGridDB's `schema/schema_map.json` / `schema/sql_codegen_map.json` /
   `schema/column_conventions.json`, regenerate, re-run its suite.
4. **Never hand-edit generated files**: `SiennaGridDB/schema/unit_registry.sql`,
   `SiennaGridDB/schema/generated_schema.sql`, and everything under `dist/`.

Deliberate, allowlisted differences between PSY and the schemas (encoded in
`check_psy_parity.py`): infra fields (`internal`, `ext`, `services`, container
fields) are dropped; schemas add integer `id`s; `Reserve{T}` direction is
flattened to a `reserve_direction` property; PSY relationship/map fields are
normalized into association components (`PlantAssociation`,
`CombinedCycleAssociation`); `Source.base_voltage` is schemas-ahead-of-PSY.

## Release order

SiennaSchemas tags first — the release tarball must include `Core/units.json`
and the `dist/` bundles. SiennaGridDB and PowerOpenAPIModels then consume the
tag. Merging or releasing in the other order leaves consumers generating from
vocabulary or components that do not exist yet.

## Known deferred items

- Dynamics component schemas (dynamics supertypes are excluded from the
  parity gate until then).
- openapi-generator templates do not render the `x-unit` vendor extension;
  the `Units:` description sentences carry units to generated code meanwhile.
- SiennaGridDB tables for `GenericArcImpedance`, `TransmissionInterface`, and
  `HybridSystem` (schemas exist; no DB tables yet).
