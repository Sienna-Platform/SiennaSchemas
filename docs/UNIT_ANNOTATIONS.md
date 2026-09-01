# Unit annotations (the `x-unit` family)

Sienna schemas carry units as JSON Schema extension keywords (the `x-` prefix
keeps them outside the validated vocabulary so draft-07 tooling ignores them).
This document specifies the family and the rules `scripts/validate_units.py`
enforces on every schema in `Core/`, `Operations/`, `Investments/`, and
`Dynamics/`.

## The vocabulary lives in `Core/units.json`

`Core/units.json` is the single source of truth for the unit vocabulary. It
lists the `quantity_types` (name, dimension, default unit) and `allowed_units`
(the exact `(quantity_type, unit)` pairs and their conversion factor to the
default unit).

**Every unit string an annotation names must be a `unit` from
`Core/units.json` `allowed_units`, or the literal `"pu"`.** No other spellings
are allowed.

## The annotation keywords

### `x-unit`

A single unit string for a numeric property. The value is either:

- a unit from the `Core/units.json` vocabulary (e.g. `"MW"`, `"kV"`, `"ohm"`,
  `"MMBtu/MWh"`), or
- the literal `"pu"` (per-unit).

A bare `"pu"` with no `x-unit-base` means the value is per-unit on the component's
own recorded base (`base_power`, or `base_current` for `TModelHVDCLine`); there is no
system-base option — components whose per-unit data was historically on the system base
record that base in `base_power`. For the four branch-impedance quantities (Resistance,
Reactance, Susceptance, Conductance) and for the power-family quantities (ActivePower,
ReactivePower, ApparentPower, and `pu/min` for ActivePowerChangeRate), `pu` **is a
registered vocabulary unit** (`to_default: null`, since the pu→natural conversion depends
on the recorded base rather than a fixed factor). Branch impedance is one of two
discriminated storage options in the downstream registry: GridDB stores each branch
parameter in either per-unit or natural units and records which per row via
`transmission_lines.parameter_units` (`COMPONENT_BASE` → `pu`; `NATURAL_UNITS` → `ohm`/`S`).
The power-family quantities are discriminated the same way at the schema layer, via each
component's own `power_units`.
Elsewhere, `"pu"` remains purely an annotation channel; no other quantity registers `pu`.

```json
"rating": { "type": "number", "x-unit": "MVA" }
"g":      { "type": "number", "x-unit": "pu" }
```

### `x-unit-base`

Names a **sibling property** that holds the natural-unit base for a per-unit
value. Use it when `"pu"` is on a device/local base rather than the system
base — for example a bus voltage in per-unit of its own `base_voltage`.

```json
"magnitude":    { "type": "number", "x-unit": "pu", "x-unit-base": "base_voltage" },
"base_voltage": { "type": "number", "x-unit": "kV" }
```

The named property must exist in the same object's `properties`.

### `x-unit-discriminator` + `x-units`

For a property whose unit depends on the value of another (sibling)
property, `x-unit-discriminator` names that sibling and `x-units` maps each
discriminator value to its unit.

The most common discriminator is `power_units`: every component with a power-family field
(active/reactive/apparent power, ratings, limits, ramp rates) carries a `power_units`
property of type `UnitSystem`, and every such field is annotated against it. Unlike
`parameter_units` and the other per-family discriminators, which typically default to
`COMPONENT_BASE` or `NATURAL_UNITS`, **`power_units` carries no default** — a producer must
always set it explicitly. A new component with power-family fields needs both: the
`power_units` property itself, and an `x-unit-discriminator`/`x-units` pair on every field it
governs.

```json
"level_data_type": { "$ref": "../../Core/common.json#/definitions/ReservoirDataType" },
"inflow": {
  "$ref": "../../Core/common.json#/definitions/MinMax",
  "x-unit-discriminator": "level_data_type",
  "x-units": {
    "USABLE_VOLUME": "m3/s",
    "TOTAL_VOLUME":  "m3/s",
    "HEAD":          "m/s",
    "ENERGY":        "MW"
  }
}
```

Rules:

- `x-unit-discriminator` must name an existing sibling property.
- The `x-units` **key set must exactly equal the discriminator's enum**. The
  discriminator may be:
  - an inline `enum`,
  - a `$ref` to an enum definition (resolved into `Core/common.json`, e.g.
    `ReservoirDataType`), or
  - a `boolean` — whose effective key set is `"true"` and `"false"`:

    ```json
    "power_mode":       { "type": "boolean" },
    "transfer_setpoint": {
      "x-unit-discriminator": "power_mode",
      "x-units": { "true": "MW", "false": "A" }
    }
    ```

- Every value in `x-units` must be in the vocabulary (or `"pu"`).

#### Nested discriminators (multi-dimensional units)

A property's unit sometimes depends on **two** siblings. The canonical case is a
VSC converter setpoint: the control mode selects the *quantity* (power vs
voltage), and for the voltage modes a unit-basis sibling then selects pu vs kV.
An `x-units` **value** may therefore be either a unit string (leaf) or a nested
`{ "x-unit-discriminator": <sibling>, "x-units": { ... } }` object:

```json
"dc_control_from": { "$ref": "../../Core/common.json#/definitions/VSCDCControlModes" },
"voltage_units":   { "$ref": "../../Core/common.json#/definitions/VoltageUnitBasis" },
"dc_setpoint_from": {
  "type": "number",
  "x-unit-discriminator": "dc_control_from",
  "x-units": {
    "DC_POWER": "MW",
    "DC_VOLTAGE":       { "x-unit-discriminator": "voltage_units",
                          "x-units": { "COMPONENT_BASE": "pu", "NATURAL_UNITS": "kV" } },
    "DC_VOLTAGE_DROOP": { "x-unit-discriminator": "voltage_units",
                          "x-units": { "COMPONENT_BASE": "pu", "NATURAL_UNITS": "kV" } }
  }
}
```

Rules (applied recursively to every nesting level):

- Each nested object must have both `x-unit-discriminator` and `x-units`.
- The nested discriminator must name an existing sibling of the annotated
  property, and its `x-units` key set must exactly equal that sibling's enum.
- Leaf values must be vocabulary units (or `"pu"`).

## Placement warning: annotations as `$ref` siblings are invisible until bundling

Under JSON Schema draft-07 and OpenAPI 3.0, any keyword placed **as a sibling
of `$ref`** is ignored — the `$ref` replaces the whole object. Most of the
`x-unit*` annotations in this repo sit next to a `$ref` (e.g. a property that
is a `MinMax`), so **downstream draft-07/OpenAPI-3.0 code generators drop them
silently**.

The fix is to bundle the specs first: `scripts/bundle_specs.py` resolves
external-file `$ref`s while **merging the sibling annotations into the
referent**, so the bundled specs consumed by codegen lose nothing.
`scripts/validate_units.py` reads the raw files and so sees these annotations
regardless of the `$ref` issue.

## Bundling: `scripts/bundle_specs.py`

For each of the four `openapi-<domain>.json` specs, the bundler emits
`dist/openapi-<domain>-bundled.json` with every **external-file `$ref`**
(a `$ref` whose value names a file, not a `#/...` internal pointer) resolved and
inlined. Rules:

- The referring node's sibling keys (`x-unit`, `x-units`, `x-unit-base`,
  `x-unit-discriminator`, `description`, ...) are **merged onto the resolved
  referent; sibling keys win over referent keys on conflict**. This is what
  recovers the `$ref`-sibling annotations that draft-07 tooling drops.
- `Core/common.json#/definitions/<Name>` targets are hoisted once into a
  top-level `definitions` block. A ref to one with **no** siblings becomes an
  internal `#/definitions/<Name>` ref; a ref that **carries** siblings is
  inlined as a merged copy (unique to that use site).
- Refs already internal to a spec (`#/...`) are left untouched.
- Output is **deterministic** (stable insertion order for walked keys; sorted
  keys for the hoisted `definitions` block), so `--check` can compare bytes
  against a fresh in-memory bundle and fail CI on stale/missing `dist/` output.

`dist/` is generated (git-ignored) — never hand-edit; regenerate with
`python scripts/bundle_specs.py`.

## The description channel (`--fix-descriptions` / `--check-descriptions`)

Descriptions are the only annotation channel that reaches generated Julia/Python
code today, so **every property carrying `x-unit` or `x-units` must have a
description ending with a canonical `Units:` sentence.**

Format:

- **Plain `x-unit`:** the description ends with `Units: <x-unit>.` — e.g.
  `... Units: MVA.`
- **Discriminated (`x-units` map):** `Units: per <discriminator> — <VAL>: <unit>, ... .`
  The `<VAL>: <unit>` pairs are the `x-units` entries in map (insertion) order,
  comma-separated, joined to the discriminator name with an em dash (`—`), and
  the sentence terminates with a space + period. Example:

  ```
  Units: per level_data_type — USABLE_VOLUME: m3/s, TOTAL_VOLUME: m3/s, HEAD: m/s, ENERGY: MW .
  ```

`scripts/validate_units.py --fix-descriptions` applies this **idempotently**: it
strips any trailing `Units: ...` sentence and re-appends the current one, so
running it twice changes nothing (a stale sentence is replaced, never
duplicated); a property with no description gets one consisting solely of the
sentence. `--check-descriptions` is the CI mode and exits non-zero if any
annotated property's description is not exactly the canonical form.

## Conventions this vocabulary encodes

- **Interchange carries natural units by default, with two deliberate per-unit
  exceptions.** Branch electrical parameters (`r`/`x`/`b`/`g`) may be stored in
  per-unit *or* natural units, and the storage layer records which per row
  (GridDB `transmission_lines.parameter_units`: `COMPONENT_BASE` → `pu`,
  `NATURAL_UNITS` → `ohm`/`S`). Every component's power-family fields
  (active/reactive/apparent power, ratings, limits, ramp rates) carry the same
  option: the component's own `power_units` — required, no default — selects
  `pu` (`COMPONENT_BASE`, against that component's `base_power`) or the
  physical unit (`NATURAL_UNITS`) for every power-family field it has.
  `Core/units.json` carries the `pu` rows for `ActivePower`, `ReactivePower`,
  `ApparentPower`, and `pu/min` for `ActivePowerChangeRate` that back this. For
  every other quantity, natural units only, and `"pu"` is a model-layer
  annotation, not a stored unit.
- **Percent is banned.** Fractions and dimensionless quantities use the unit
  `"1"` and are stored as fractions (`0.95`, not `95`). There is no `"%"` unit.
- **`unit` / `units` string properties.** Any property literally named `unit`
  or `units` and typed `string` (e.g. `TimeSeries/SingleTimeSeries.json`'s
  `units`) must have a description pointing at `Core/units.json` — its value
  is a free-text unit string that must come from the vocabulary. This is
  enforced as rule 6. (`TimeSeries/TimeSeriesAssociation.json` is the `oneOf`
  wrapper over the six per-type schemas and carries no `units` property of
  its own, so it is not this rule's example.)

## Maintaining the vocabulary

To add or change a unit:

1. **Edit `Core/units.json`** — add the `quantity_type` and/or the
   `allowed_units` `(quantity_type, unit, to_default)` row. This is the only
   place the vocabulary is defined.
2. **Regenerate the SiennaGridDB unit registry** from the updated
   `Core/units.json` (GridDB's `scripts/generate_unit_registry.py`) so the
   database's `quantity_types` / `allowed_units` tables stay in sync.

Never introduce a unit spelling in a schema annotation without first adding it
to `Core/units.json`; `scripts/validate_units.py` will reject it.

## What the validator checks

`scripts/validate_units.py` (stdlib + `jsonschema` only) enforces, over every
schema JSON under `Core/`, `Operations/`, `Investments/`, `Dynamics/`
(excluding `Core/units.json` and the `openapi-*.json` specs):

1. Each schema validates against the **draft-07 meta-schema**.
2. Every `x-unit` / `x-units` value is in `Core/units.json` `allowed_units`, or
   `"pu"`.
3. Every `x-unit-base` and `x-unit-discriminator` names an existing sibling
   property.
4. Every `x-units` key set exactly equals the discriminator's enum (resolving a
   `$ref` discriminator into `Core/common.json`; a boolean discriminator has
   keys `"true"` / `"false"`).
5. No `"descriptor"` keys and no `"type": null` anywhere.
6. Every `unit` / `units` string property has a description mentioning
   `Core/units.json` or the vocabulary.

It runs in CI (`.github/workflows/validate-schemas.yml`) on every push and pull
request and exits non-zero on any failure.
