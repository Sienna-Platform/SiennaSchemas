---
name: contribute-schemas
description: Add, extend, or change a SiennaSchemas schema — a new component, supplemental attribute, association, shared value type, or unit — then run the gates and regenerate the OpenAPI model packages and the SQL tables. Use when asked to add a component or field to the schemas, decide whether something is a component or an attribute, annotate units, regenerate the Python/Julia models, or create the GridDB table for a new schema.
---

# Contributing to SiennaSchemas

This repo is hand-written source. The Python models, the Julia models, and the
SQL registry are all **generated from it**, so a contribution is never one file:
it is a schema edit, six gates, and up to three regeneration legs.

All paths are relative to the SiennaSchemas checkout root. The driver is
`.claude/skills/contribute-schemas/pipeline.py` — it finds sibling repos next to
this one and skips cleanly when they are absent.

Start here:

```bash
python3 .claude/skills/contribute-schemas/pipeline.py doctor
```

It reports which interpreter runs the gates, which `datamodel-codegen` can run
the Python codegen, and whether the Julia codegen image exists.

## Step 1 — decide what you are adding

Most of the cost of a bad contribution is choosing the wrong kind. Answer in
order; the first yes wins.

| Ask | If yes | Lives in | Also touches |
|---|---|---|---|
| Does it have its own identity — other things reference it by integer id, it can own time series? | **Component** | `<Domain>/<Group>/X.json` | selector; a GridDB table |
| Is it descriptive data hung off components — several per component, or one shared by many? | **Supplemental attribute** | `<Domain>/SupplementalAttributes/X.json` | selector only |
| Does it carry nothing but two foreign keys, normalizing a many-to-many? | **Association** | `<Domain>/Associations/X.json` | selector; a new bucket in `Core/SystemDocument.json` |
| Is it a field group or enum that several components share? | **`$defs` entry in `Core/common.json`** | — | `openapi-core.json`, or infra-core (see below) |
| Is it a unit or a quantity type? | **row in `Core/units.json`** | — | nothing here; GridDB's registry regenerates |

Reading the table:

- **Component vs supplemental attribute** is the common confusion. A component
  participates in the system and is addressable. An attribute *describes* a
  component and is linked through the existing
  `Core/Associations/SupplementalAttributeAssociation.json` — you never write a
  new link type for one. `EmissionsData` is an attribute; `ThermalStandard` is a
  component. If you find yourself wanting a `component_id` field on your new
  type so it can point at its owner, you want an attribute, not a component.
- **Association** is for many-to-many only. A one-to-many relation is an integer
  `_id` / `_ids` property on the owning component — no association type.
- Groups that exist today: `Operations/{Topology,Branch,StaticInjection,Service,Market,SupplementalAttributes,Associations}`,
  `Investments/{Technologies,Financials,Requirements,Attributes,Regions}`,
  `Core/{SupplementalAttributes,Associations}`,
  `Dynamics/{DynamicGeneratorComponent,DynamicInverterComponent}`.
- Putting a shared `$def` in **infrastructure-core** (the domain-neutral layer)
  is a layering decision, not a drive-by: `scripts/check_layering.py` pins that
  set to exactly 20 members and fails on an unlisted addition. Widening it means
  editing `INFRA_MEMBERS` in that script deliberately.

## Step 2 — the rules that have no escape hatch

**No abstractions.** There is no base `Device` or `Component` schema and no
inheritance anywhere. Every component repeats `id`, `name`, and `available`
inline. Do not factor them out into a shared base and `$ref` it — that is the
change this repo has consistently declined to make.

- `allOf` is not composition here. The one use in the tree
  (`Operations/SupplementalAttributes/EmissionsData.json`) is an `if`/`then`
  conditional constraint, not a supertype.
- `oneOf` + `discriminator` is for **value** unions — cost curves, function
  data, the time series wrapper. It never gives components a common parent.
- Reuse is by embedding: a shared `$def` or a shared file becomes a *property
  value* (`MinMax`, `TechnologyFinancialData`), never a supertype.

**No derived or read-only fields.** The upstream data model has them; this layer
does not. If a value is computable from other fields, leave it out.

**Every numeric carries a unit.** `x-unit`, or `x-units` + `x-unit-discriminator`
for the discriminated cases. The unit string must already exist in
`Core/units.json` — add it there first or the gate rejects it. Reactive power is
`MVAr`. Percent is banned; store fractions with `x-unit: "1"`. Read
`docs/UNIT_ANNOTATIONS.md` before inventing an annotation form.

**Power-family fields need a `power_units` sibling.** Any component with
active/reactive/apparent power, ratings, limits, or ramp rates carries a
`power_units` property of type `UnitSystem` — with **no default** — and every
such field is annotated against it:

```json
"power_units": { "$ref": "../../Core/common.json#/$defs/UnitSystem" },
"active_power": {
  "type": "number",
  "x-unit-discriminator": "power_units",
  "x-units": { "NATURAL_UNITS": "MW", "COMPONENT_BASE": "pu" }
}
```

**Never hand-edit generated output**: `dist/`, both model packages,
`SiennaGridDB/schema/unit_registry.sql`, `SiennaGridDB/schema/generated_schema.sql`.

**Directory layout is load-bearing.** Cross-references are relative paths.
Moving a file breaks every `$ref` to it.

## Step 3 — scaffold

```bash
python3 .claude/skills/contribute-schemas/pipeline.py scaffold component StaticVarCompensator \
  --domain Operations --group StaticInjection \
  --description "A shunt device that supplies or absorbs reactive power."
```

`scaffold <kind> <Name>` where kind is `component`, `attribute`, or
`association`. It writes the stub with the right base properties for that kind
and inserts the `$ref` into the domain's `openapi-*.json` selector
alphabetically. Then you fill in the real properties by hand.

There is no scaffold for a shared `$def` or a unit — those are single-line edits
to `Core/common.json` and `Core/units.json`.

## Step 4 — fix and gate

```bash
python3 .claude/skills/contribute-schemas/pipeline.py fix
python3 .claude/skills/contribute-schemas/pipeline.py check
```

`fix` regenerates the canonical `Units:` description sentences and rebundles
`dist/`. Do not write those sentences by hand — the generator owns their exact
punctuation, down to the em dash and the space before the final period.

`check` runs all six gates and prints one line each: unit annotations,
description channel, bundle freshness, `$ref` resolution, package layering, and
time series fixtures. Fix everything before moving on; the downstream legs
assume these pass.

### Ambiguous units need `x-quantity`

A unit does not always say what the number *is*. `pu` names eight quantity types
(ActivePower, ApparentPower, Conductance, Reactance, ReactivePower, Resistance,
Susceptance, Voltage), `"1"` names three (Dimensionless, Fraction, PowerFactor),
and `ohm`, `S`, and `m` are ambiguous too. Where the unit alone is not enough,
the schema says which quantity it means:

```json
"magnitude": { "type": "number", "x-unit": "pu", "x-quantity": "Voltage" }
```

**You usually do not have to write it.** Within one `x-units` map, an
unambiguous branch settles its ambiguous siblings, so the standard power-family
shape needs nothing extra — `MW` is unambiguously ActivePower, so the `pu`
branch is too:

```json
"x-units": { "NATURAL_UNITS": "MW", "COMPONENT_BASE": "pu" }
```

`validate_units.py` (gate 1) tells you exactly when a declaration is required:

```
FAIL [x-quantity-required] Operations/Topology/ACBus.json
     path:     /properties/magnitude/x-quantity
     got:      absent; unit 'pu' names 8 quantities
     expected: "x-quantity": one of ActivePower, ApparentPower, ...
```

It also rejects a declaration that contradicts the sibling branches, and one on
a property whose units are already unambiguous. Full rules:
`docs/UNIT_ANNOTATIONS.md`, rule 7.

## Step 5 — regenerate the models and the SQL

```bash
python3 .claude/skills/contribute-schemas/pipeline.py downstream
```

Or one leg at a time with `--legs db`, `--legs py`, `--legs jl`.

**db** regenerates `unit_registry.sql` and `generated_schema.sql` in
SiennaGridDB and runs the units sync check. A new component needs a
`schema/schema_map.json` entry first, or it is invisible to the DB leg:

```json
"static_var_compensators": [
  { "component": "StaticVarCompensator",
    "file": "Operations/StaticInjection/StaticVarCompensator.json",
    "is_psy": true }
]
```

`generated_schema.sql` is a **reference artifact, not the applied DDL**. The
production schema is the hand-written `schema/schema.sql`. The driver prints a
`TODO` naming each proposed table that is missing from it; copy the generated
`CREATE TABLE` block across, add the matching `DROP TABLE`, and bump
`PRAGMA user_version`. Column renames and foreign keys go in
`schema/sql_codegen_map.json`; unit rows go in `schema/column_conventions.json`.

**py** runs `make generate && make validate` in `power-openapi-models`.

**jl** runs the Dockerized `openapi-generator` for `PowerOpenAPIModels`. Validate
it separately (see Gotchas — `make validate` there needs a Julia env you supply):

```bash
julia --project=/path/to/env-with-TimeZones test/validate.jl
```

Both model repos should be committed with a clean, reviewed diff; the driver
reports how many files each leg changed.

## Gotchas

- **The system `python3` cannot run the gates.** It has no `jsonschema`. The
  repo's own `.venv/bin/python3` does. The driver finds the right one; if you
  call the scripts directly, use `.venv/bin/python3 scripts/validate_units.py`.
- **Ambiguous units need `x-quantity`** — see above. Gate 1 catches it, so it
  should never reach the Docker run. The Python leg ignores the annotation
  entirely, so a green Python run proves nothing here.
- **`power-openapi-models/.venv` is too old for its own Makefile.** Its pinned
  `datamodel-codegen` is 0.55.0, which rejects the `--allow-remote-refs` flag the
  Makefile passes. 0.71.0 (in the workspace-root `.venv`) works and reproduces
  the committed output byte-for-byte. The driver picks the working one.
- **The ghcr codegen image is auth-gated.** `docker pull
  ghcr.io/sienna-platform/power-codegen:latest` returns `denied`. Build it
  locally instead — the Dockerfile is self-contained:
  `cd ../PowerOpenAPIModels && docker build -t power-codegen:local .` The driver
  looks for exactly that tag.
- **`make validate` in PowerOpenAPIModels uses the global Julia environment**
  and fails with `Package TimeZones not found`. It also `Pkg.develop`s the six
  packages into whatever env is active, so point it at a scratch one:
  `julia --project=$SCRATCH -e 'using Pkg; Pkg.add(["TimeZones","Test","Dates"])'`
  then `julia --project=$SCRATCH test/validate.jl`.
- **Adding a component that `$ref`s a shared type can silently duplicate it.**
  openapi-generator names inline aliases by occurrence count, so a new use of
  `MinMax` may emit `MinMax_3`, which needs an `inlineSchemaNameMappings` entry
  in `openapi-config-<domain>.json`. The count is empirical — nothing here can
  derive it. The `No unmapped inline schema aliases` testset in
  `PowerOpenAPIModels/test/validate.jl` is the only thing that catches it, which
  is why the Julia validate is not optional.
- **`validate_units.py` walks the directory tree, not the selector.** A schema
  file passes the unit gates before you have registered it anywhere. `check_refs`
  and the codegen legs are what notice it is missing from the selector.
- **The unit-quantity pairing rule silently skips without a GridDB checkout**,
  and CI has none. A green CI run is not evidence that rule holds. The driver
  runs against the sibling checkout when it is there.
- **Release order is fixed.** SiennaSchemas tags first; GridDB and the model
  packages consume the tag. Regenerating downstream before the tag exists leaves
  consumers generating from vocabulary that is not released.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'jsonschema'` | Use `.venv/bin/python3`, or `pipeline.py`, which finds it. |
| `FAIL [units-description-sentence] ... expected: '... Units: MVA.'` | `pipeline.py fix`. Never hand-write the sentence. |
| `bundle_specs.py --check` fails | You edited a schema and did not rebundle. `pipeline.py fix`. |
| `declares ambiguous x-unit="pu" ... Declare "x-quantity"` from the Julia codegen | You skipped `check`. Run it — gate 1 names the property and the candidates. |
| `FAIL [x-quantity-required]` | Add `"x-quantity"` to that property, picking from the listed quantity types. |
| `FAIL [x-quantity-contradicts-siblings]` | Your declaration disagrees with what the unambiguous branch implies; one of the two is wrong. |
| `FAIL [x-quantity-unnecessary]` | That unit names exactly one quantity — delete the declaration. |
| `datamodel-codegen: error: unrecognized arguments: --allow-remote-refs` | Wrong venv on `PATH`; use the workspace-root `.venv/bin`. |
| `Error response from daemon: denied` on docker pull | Build `power-codegen:local` from the Dockerfile. |
| `Package TimeZones not found` | `make validate` uses the global Julia env; use `--project` with a scratch env. |
| Your new table never appears in `generated_schema.sql` | You did not add it to `SiennaGridDB/schema/schema_map.json`. |
| `check_layering.py` fails on a new `$def` | You added it to `openapi-infrastructure-core.json`; either move it to `openapi-core.json` or add it to `INFRA_MEMBERS` deliberately. |
