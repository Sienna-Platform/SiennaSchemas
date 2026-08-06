#!/usr/bin/env python3
"""Check structural parity between PowerSystemsInvestmentsPortfolios.jl and these schemas.

Every type in PSIP's `SiennaInvestSchema.json` descriptor must have a schema
component of the same title, the matched pair must have no unexplained field
drift, and each schema's `required` list must be derivable from the descriptor.

Usage:
  python3 scripts/check_psip_parity.py --psip-path ../PowerSystemsInvestmentsPortfolios.jl

When the PSIP checkout is absent the check SKIPs cleanly (exit 0), mirroring
check_psy_parity.py, so CI can run it unconditionally.

`required` is derived rather than compared verbatim. Both repos use the same
rule -- a field is required exactly when it has no default -- but only PSIP
carries the default values, so the descriptor is the side that can answer it:

    required := { PSIP properties with no `default` } | { id }

Output contract:
  MISSING SCHEMA <TypeName>     PSIP type with no schema component
  MISSING STRUCT <Title>        schema component with no PSIP type
  FIELD DRIFT <Name>: psip_only=[...] schema_only=[...]
  REQUIRED DRIFT <Name>: expected=[...] actual=[...]
  UNIT DRIFT <Name>.<field>: conversion_unit=<v> x-unit=<v>   units disagree
  UNIT DRIFT <Name>.<field>: x-unit=<v> but PSIP declares no needs_conversion
  WARN ...                      known upstream gaps; reported, never fatal
  SUMMARY: N missing schemas, M unexplained drifts, U unit drifts
Exit 1 if N + M + U > 0.

Unit parity is checked because PSIP's units are ABSOLUTE (MW, USD/MWh, t) rather
than the relative per-unit bases check_psy_parity.py deals with, so each
`conversion_unit` admits exactly one `x-unit` and disagreement is unambiguous. A
mismatch here is a silent wrong magnitude -- no error, no failing test -- which is
why it is fatal rather than a WARN.
"""

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# IS implementation details. PSIP declares them on every type; SiennaSchemas
# stores `ext` separately and never serializes `internal`, so they appear in
# zero schema files here. Excluded from both sides of every comparison.
EXCLUDED_FIELDS = {"ext", "internal"}

# Schema components with no descriptor entry. These are plain Julia structs
# (src/models/financial_data/, src/portfolio.jl) rather than descriptor-generated
# types, so their fields are read from the struct definitions instead. Every
# field is positional with no default, hence all are required.
FINANCIAL_FIELDS = {
    "PortfolioFinancialData": [
        "base_year",
        "discount_rate",
        "inflation_rate",
        "interest_rate",
    ],
    "TechnologyFinancialData": [
        "capital_recovery_period",
        "technology_base_year",
        "debt_fraction",
        "debt_rate",
        "return_on_equity",
        "tax_rate",
    ],
}

DESCRIPTOR_RELPATH = os.path.join("src", "descriptors", "SiennaInvestSchema.json")


def _descriptor_nodes(components):
    """name -> node, for either descriptor shape.

    PSIP's descriptor states `components` as a JSON *array* of entries carrying their own
    `name`. An OpenAPI-shaped mapping of title -> node is also accepted so this checker keeps
    working if the descriptor is ever bundled that way. Any other shape is a hard error --
    silently returning nothing would make the whole check pass while comparing zero types.
    """
    if isinstance(components, list):
        nodes = {}
        for entry in components:
            if not isinstance(entry, dict) or "name" not in entry:
                raise ValueError(
                    f"descriptor `components` entry is not a named object: {entry!r}"
                )
            nodes[entry["name"]] = entry
        return nodes
    if isinstance(components, dict):
        return components.get("schemas", components)
    raise ValueError(
        f"descriptor `components` is {type(components).__name__}; expected list or dict"
    )


def _node_properties(node):
    """field -> spec, for either property shape (list of named dicts, or a mapping)."""
    props = node.get("properties", {})
    if isinstance(props, list):
        out = {}
        for spec in props:
            if not isinstance(spec, dict) or "name" not in spec:
                raise ValueError(
                    f"property entry is not a named object: {spec!r}"
                )
            out[spec["name"]] = spec
        return out
    return props


def load_psip_types(psip_path):
    """name -> (properties, properties-with-a-default, declared required)."""
    with open(os.path.join(psip_path, DESCRIPTOR_RELPATH)) as handle:
        schemas = _descriptor_nodes(json.load(handle)["components"])
    types = {}
    for name, node in schemas.items():
        props = {
            field: spec
            for field, spec in _node_properties(node).items()
            if field not in EXCLUDED_FIELDS
        }
        defaulted = {field for field, spec in props.items() if "default" in spec}
        declared = set(node.get("required", [])) - EXCLUDED_FIELDS
        types[name] = (set(props), defaulted, declared, props)
    return types


def load_schema_components():
    """title -> (properties, required, property specs) for every Investments component."""
    with open(os.path.join(REPO_ROOT, "openapi-investments.json")) as handle:
        spec = json.load(handle)
    components = {}
    for title, node in spec["components"]["schemas"].items():
        ref = node.get("$ref")
        if not ref:
            continue
        with open(os.path.join(REPO_ROOT, ref)) as handle:
            schema = json.load(handle)
        properties = schema.get("properties", {})
        components[title] = (
            set(properties),
            list(schema.get("required", [])),
            properties,
        )
    return components


# PSIP's units are ABSOLUTE (MW, USD/MWh, t), not relative per-unit bases, so unlike
# check_psy_parity.py's family logic each `conversion_unit` admits exactly one `x-unit`.
# The two vocabularies are mechanically related -- `:usd_per_mwh` <-> `USD/MWh` -- so
# normalizing both sides beats a lookup table, which would be a third source of truth
# for the unit names and would rot independently of either side.
DIMENSIONLESS_UNITS = {"1", ""}


def normalize_unit(value):
    """Canonical form for comparing a `conversion_unit` symbol with an `x-unit` string."""
    return value.lstrip(":").replace("_per_", "/").lower()


def check_unit_parity(name, psip_specs, schema_specs):
    """Report unit disagreements for one type; return the number found.

    Two classes, both silent-wrong-magnitude risks: a field annotated on both sides whose
    units disagree, and a unit-bearing schema field PSIP never marks `needs_conversion`
    (so its accessor performs no conversion at all).
    """
    found = 0
    for field, schema_spec in sorted(schema_specs.items()):
        if field in EXCLUDED_FIELDS:
            continue
        psip_spec = psip_specs.get(field)
        if psip_spec is None:
            continue
        x_unit = schema_spec.get("x-unit")
        conversion_unit = psip_spec.get("conversion_unit")
        needs_conversion = bool(psip_spec.get("needs_conversion"))
        if x_unit is None or x_unit in DIMENSIONLESS_UNITS:
            continue
        if not needs_conversion:
            print(
                f"UNIT DRIFT {name}.{field}: x-unit={x_unit} but PSIP declares no "
                "needs_conversion"
            )
            found += 1
            continue
        if normalize_unit(conversion_unit) != normalize_unit(x_unit):
            print(
                f"UNIT DRIFT {name}.{field}: "
                f"conversion_unit={conversion_unit} x-unit={x_unit}"
            )
            found += 1
    return found


def expected_required(name, psip_types, props):
    """The `required` list this schema should carry."""
    if name in psip_types:
        all_props, defaulted, _, _ = psip_types[name]
        req = (all_props - defaulted) | {"id"}
    else:
        req = set(FINANCIAL_FIELDS[name]) | {"id"}
    return req & props


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--psip-path",
        default=os.path.normpath(
            os.path.join(REPO_ROOT, "..", "PowerSystemsInvestmentsPortfolios.jl")
        ),
        help="PowerSystemsInvestmentsPortfolios.jl checkout (SKIPs cleanly when absent)",
    )
    args = parser.parse_args()

    descriptor = os.path.join(args.psip_path, DESCRIPTOR_RELPATH)
    if not os.path.exists(descriptor):
        print(f"SKIP: PSIP checkout not found at {args.psip_path}")
        return 0

    psip_types = load_psip_types(args.psip_path)
    components = load_schema_components()

    missing_schemas = 0
    drifts = 0
    unit_drifts = 0

    for name in sorted(set(psip_types) - set(components)):
        print(f"MISSING SCHEMA {name}")
        missing_schemas += 1

    for title in sorted(set(components) - set(psip_types)):
        if title in FINANCIAL_FIELDS:
            continue
        print(f"MISSING STRUCT {title}")
        drifts += 1

    missing_id = []
    for name in sorted(set(psip_types) & set(components)):
        psip_props = psip_types[name][0]
        props, required, schema_specs = components[name]
        unit_drifts += check_unit_parity(name, psip_types[name][3], schema_specs)

        psip_only = sorted(psip_props - props)
        # `id` is a SiennaSchemas base property (id/name/available) present on
        # every component; six PSIP attribute types omit it from the descriptor.
        schema_only = sorted(prop for prop in props - psip_props if prop != "id")
        if "id" not in psip_props:
            missing_id.append(name)
        if psip_only or schema_only:
            print(
                f"FIELD DRIFT {name}: psip_only={psip_only} schema_only={schema_only}"
            )
            drifts += 1

    for name in sorted(components):
        if name not in psip_types and name not in FINANCIAL_FIELDS:
            continue
        props, required, _ = components[name]
        expected = expected_required(name, psip_types, props)
        if set(required) != expected:
            print(
                f"REQUIRED DRIFT {name}: "
                f"expected={sorted(expected)} actual={sorted(required)}"
            )
            drifts += 1

    if missing_id:
        print(
            f"WARN id absent from PSIP descriptor for {len(missing_id)} type(s): "
            f"{missing_id}"
        )

    # PSIP's hand-written `required` is not used to drive anything here, and it
    # disagrees with the defaults in its own descriptor -- a field can be absent
    # from `required` yet have no default, so the generated constructor demands
    # it. Surfaced so the upstream inconsistency stays visible.
    inconsistent = [
        name
        for name, (props, defaulted, declared, _) in sorted(psip_types.items())
        if declared != (props - defaulted)
    ]
    if inconsistent:
        print(
            f"WARN PSIP declared `required` disagrees with its own defaults for "
            f"{len(inconsistent)}/{len(psip_types)} type(s); descriptor defaults used instead"
        )

    print(
        f"SUMMARY: {missing_schemas} missing schemas, {drifts} unexplained drifts, "
        f"{unit_drifts} unit drifts"
    )
    return 1 if (missing_schemas + drifts + unit_drifts) > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
