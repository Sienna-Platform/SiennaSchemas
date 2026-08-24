#!/usr/bin/env python3
"""Check structural parity between PowerSystems.jl structs and these schemas.

Every non-dynamics PSY struct (generated structs from the descriptor plus the
hand-written supplemental attributes) must have a schema component of the same
title, and the matched pair must have no unexplained field drift. Known,
deliberate conventions are allowlisted below; anything else fails the check.

Usage:
  python3 scripts/check_psy_parity.py --psy-path ../PowerSystems.jl

When the PSY checkout is absent the check SKIPs cleanly (exit 0), mirroring
check_units_sync.py's optional PSY layer, so CI can run it unconditionally.

Output contract:
  MISSING SCHEMA <StructName>   PSY struct with no schema component
  MISSING STRUCT <Title>        schema component with no PSY struct
  FIELD DRIFT <Name>: psy_only=[...] schema_only=[...]
  DEFAULT DRIFT <Name>.<field>: psy=<v> schema=<v>   numeric default mismatch
  CONVERTER DRIFT missing <Type>       registered hand-written but no from_openapi found
  CONVERTER DRIFT unregistered <Type>  from_openapi found but not registered anywhere
  CONVERTER DRIFT overlap <Type>       both openapi_type-annotated and hand-written registered
  UNIT DRIFT <Type>.<field>: conversion_unit=<v> x-unit=<v>   family mismatch
  UNIT DRIFT <Type>.<field>: openapi_unit=<v> x-unit=<v>      pu-identity mismatch
  SUMMARY: N missing schemas, M unexplained drifts
Exit 1 if N + M > 0.
"""

import argparse
import json
import os
import re
import sys

from validate_units import collect_schema_files

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# PSY abstract dynamics-component types live in these model files as
# `abstract type <Name> <: DynamicComponent/DynamicInjection/...`. Every struct
# whose descriptor supertype is one of them is a dynamics modeling component,
# which schemas deliberately do not cover yet (Dynamics annotation is deferred).
DYNAMICS_MODEL_FILES = (
    "src/models/dynamic_models.jl",
    "src/models/dynamic_inverter.jl",
    "src/models/dynamic_inverter_components.jl",
    "src/models/dynamic_generator_components.jl",
)

# Fallback used only when the dynamics model sources are absent (partial PSY
# checkout): the abstract supertype names cannot be derived, so exclude by this
# known list instead. Kept in sync with the abstract types in the files above.
DYNAMICS_SUPERTYPES_FALLBACK = {
    "AVR",
    "ActivePowerControl",
    "Converter",
    "DCSource",
    "DynamicInjection",
    "Filter",
    "FrequencyEstimator",
    "InnerControl",
    "Machine",
    "OutputCurrentLimiter",
    "PSS",
    "ReactivePowerControl",
    "Shaft",
    "TurbineGov",
}


def derive_dynamics_supertypes(psy_path):
    """Abstract dynamics-component type names declared in PSY's dynamic model
    files. Falls back to DYNAMICS_SUPERTYPES_FALLBACK if none are found (the
    sources are missing in a partial checkout)."""
    names = set()
    for rel in DYNAMICS_MODEL_FILES:
        full = os.path.join(psy_path, rel)
        if not os.path.exists(full):
            continue
        with open(full, encoding="utf-8") as f:
            text = f.read()
        for match in re.finditer(r"abstract type\s+([A-Za-z_][A-Za-z0-9_]*)\b", text):
            names.add(match.group(1))
    if not names:
        return set(DYNAMICS_SUPERTYPES_FALLBACK)
    return names


# PSY infrastructure fields never represented as schema properties.
DROPPED_FIELDS = {
    "internal",
    "ext",
    "time_series_container",
    "supplemental_attributes_container",
    "services",
}

# PSY field names respelled as schema property names: unicode transliterated
# to ASCII, and object references renamed to the integer-id `_id` convention
# (`from`/`to` exist only on Arc, so the global map is safe).
TRANSLITERATION = {
    "α": "alpha",
    "β": "beta",
    "α_primary": "alpha_primary",
    "α_secondary": "alpha_secondary",
    "α_tertiary": "alpha_tertiary",
    "from": "from_id",
    "to": "to_id",
}

# PSY encodes reserve direction in the Reserve{T} type parameter; schemas
# flatten it into a reserve_direction property. OfflineReserve is upward-only
# (no ReserveDirection type parameter), so it is deliberately excluded.
RESERVE_DIRECTION_COMPONENTS = {
    "OnlineReserve",
    "GroupReserve",
}

# PSY relationship/map fields normalized into association components
# (PlantAssociation / CombinedCycleAssociation) instead of properties.
ASSOCIATION_NORMALIZED = {
    "AGC": {"reserves"},
    "GroupReserve": {"contributing_services"},
}
PLANT_SA_STRUCTS = {
    "ThermalPowerPlant",
    "CombinedCycleBlock",
    "CombinedCycleFractional",
    "HydroPowerPlant",
    "RenewablePowerPlant",
}

# Deliberate schemas-ahead-of-PSY properties.
# Fields intentionally present only in the schema, with no PowerSystems.jl
# counterpart. Unit-basis discriminators (any property whose $ref ends in
# "UnitBasis") are exempted structurally in explained_schema_only, so they
# never need an entry here.
#
# base_power on Line/MonitoredLine/GenericArcImpedance/DiscreteControlledACBranch
# records the SYSTEM base per component, in lieu of a system-level table/JSON
# entry -- the deliberate schema design choice. PSY now ALSO stores base_power
# on these four types (added by add_component!, kept in sync with the system
# base -- see PSY's branchdata_checks.jl / components.jl BasePowerKind trait),
# so these four entries are not currently required for the checker to pass.
# They stay recorded anyway: the SEMANTIC being flagged (a schema field that
# records the system base per component, not the device base) is the actual
# deliberate exception, independent of whether PSY happens to carry a
# same-named field. Remove an entry only if the schema stops recording the
# system base on that component.
#
# TModelHVDCLine was the exception among the branches -- it per-unitizes
# against a base current, not a power base -- but base_current now exists on
# both sides (PSY dropped base_power for this type and added base_current),
# so it no longer needs an entry here.
SCHEMA_AHEAD = {
    "Source": {"base_voltage"},
    "Line": {"base_power"},
    "MonitoredLine": {"base_power"},
    "GenericArcImpedance": {"base_power"},
    "DiscreteControlledACBranch": {"base_power"},
}

# PSY fields that are constructor-managed runtime state, never serialized, so
# schemas never represent them. TransformerCircuit.base_value is repopulated by
# add_component! and explicitly skipped by PSY's hand-written IS.serialize
# (src/models/transformer_circuits.jl). Reviewable per type, like SCHEMA_AHEAD.
PSY_INTERNAL = {
    "TransformerCircuit": {"base_value"},
}

# Schema components with no PSY struct by design (association normalization
# and IS-level concepts). Everything under Investments/ maps to PSIP, not PSY,
# and is excluded from the scan entirely.
SCHEMA_ONLY_COMPONENTS = {
    "CombinedCycleAssociation",
    "PlantAssociation",
    # Reserve/service participation is normalized to (service_id, entity_id) rows
    # here. PowerSystems keeps the same relation on the device side as
    # Device.services, so there is no PSY struct to match.
    "ServiceAssociation",
    "SupplementalAttributeAssociation",
    # The time series association family: JSON metadata rows, not components.
    # All seven are allowlisted, not just the wrapper -- PSY re-exports
    # SingleTimeSeries, Deterministic, DeterministicSingleTimeSeries,
    # Probabilistic, and Scenarios from InfrastructureSystems, so without these
    # the gate finds same-named Julia structs and compares a metadata schema
    # against a struct that carries data arrays.
    "TimeSeriesAssociation",
    "SingleTimeSeries",
    "NonSequentialTimeSeries",
    "Deterministic",
    "DeterministicSingleTimeSeries",
    "Probabilistic",
    "Scenarios",
    # The whole-system serialization envelope, not a component. Its Julia
    # counterpart is the hand-written container in the umbrella
    # PowerOpenAPIModels.jl package (src/document.jl), checked against the
    # schema by that repo's own validate.jl -- there is no PSY struct to match.
    "SystemDocument",
}

# Hand-written supplemental-attribute structs: name -> Julia source relative
# to the PSY checkout.
HAND_WRITTEN = {
    "EmissionsData": "src/emissions_data.jl",
    "ImpedanceCorrectionData": "src/impedance_correction.jl",
    "PlannedOutage": "src/outages.jl",
    "GeometricDistributionForcedOutage": "src/outages.jl",
    "FixedForcedOutage": "src/outages.jl",
    "ThermalPowerPlant": "src/plant_attribute.jl",
    "CombinedCycleBlock": "src/plant_attribute.jl",
    "CombinedCycleFractional": "src/plant_attribute.jl",
    "HydroPowerPlant": "src/plant_attribute.jl",
    "RenewablePowerPlant": "src/plant_attribute.jl",
    "Substation": "src/substation.jl",
    # OnlineReserve/OfflineReserve/GroupReserve are parametric (ReserveDirection,
    # and for OnlineReserve/OfflineReserve also the cost-curve unit type), so the
    # struct entry the descriptor carries lives under `struct_validation_descriptors`
    # (field-validation metadata only), not `auto_generated_structs` — there is no
    # generated struct file, same as the supplemental attributes above.
    "OnlineReserve": "src/models/reserves.jl",
    "OfflineReserve": "src/models/reserves.jl",
    "GroupReserve": "src/models/reserves.jl",
}
# IS-resident hand-written supplemental-attribute structs: name -> Julia
# source relative to the InfrastructureSystems.jl checkout.
IS_HAND_WRITTEN = {
    "GeographicInfo": "src/geographic_supplemental_attribute.jl",
    "DataSource": "src/data_source_supplemental_attribute.jl",
}

# Hand-written `from_openapi` CONVERTERS (src/openapi/import_handwritten.jl):
# a DIFFERENT category from HAND_WRITTEN above. Every one of these has a normal
# generated struct (an `auto_generated_structs` entry, or — for the three
# reserves — a `struct_validation_descriptors` entry); what's hand-written is
# only the PO<->PSY conversion function, because the IS generator's
# `openapi_type` mechanism cannot emit it (field-name mismatch, non-scalar
# field, missing base_power anchor, or a parametric struct — see the header
# comment of openapi/import_handwritten.jl for the reason per type).
# Derived from that source; check_converter_coverage below re-derives it and
# fails on any drift.
HAND_WRITTEN_CONVERTERS = {
    "Arc",
    "Area",
    "LoadZone",
    "Line",
    "TransformerCircuit",
    "TwoWindingTransformer",
    # magnetizing_shunt::Complex{Float64} (same issue as TwoWindingTransformer) plus
    # three TransformerCircuit references (primary/secondary/tertiary_circuit) instead
    # of the generator's single-reference assumption.
    "ThreeWindingTransformer",
    "FixedAdmittance",
    "HydroReservoir",
    "EnergyReservoirStorage",
    "TwoTerminalGenericHVDCLine",
    # direction_mapping::Dict{String, Int} is unclassifiable to the generator (not
    # scalar/compound/reference/enum); also carries its own base_power field with a
    # system-base fallback (_resolve_base_power), same pattern as Area/LoadZone.
    "TransmissionInterface",
    "OnlineReserve",
    "OfflineReserve",
    "GroupReserve",
    # Declared by the PSY "OpenAPI serde: converters for six more types" work: each has a
    # `from_openapi` method in import_handwritten.jl but was not listed here.
    "DiscreteControlledACBranch",
    "FACTSControlDevice",
    "SwitchedAdmittance",
    "TwoTerminalLCCLine",
    "TwoTerminalVSCLine",
    "GenericArcImpedance",
    "HybridSystem",
    "InterconnectingConverter",
    "MonitoredLine",
    "Source",
    "TModelHVDCLine",
}
HANDWRITTEN_CONVERTERS_REL_PATH = "src/openapi/import_handwritten.jl"


def derive_handwritten_converter_types(psy_path):
    """Type names with an actual `from_openapi(po::PO.X, ...)` method in
    `src/openapi/import_handwritten.jl`. Returns None when the file is
    absent (partial checkout), so callers can skip the drift check instead of
    reporting spurious CONVERTER DRIFT lines."""
    full = os.path.join(psy_path, HANDWRITTEN_CONVERTERS_REL_PATH)
    if not os.path.exists(full):
        return None
    with open(full, encoding="utf-8") as f:
        text = f.read()
    return set(re.findall(r"from_openapi\(\s*po::PO\.(\w+)", text))


def derive_openapi_annotated_types(descriptor):
    """Struct names carrying an `openapi_type` annotation on their
    `auto_generated_structs` entry: the generated-converter half of converter
    coverage, complementing HAND_WRITTEN_CONVERTERS."""
    return {
        entry["struct_name"]
        for entry in descriptor["auto_generated_structs"]
        if "openapi_type" in entry
    }


# The three quantity families (POWER/IMPEDANCE/ADMITTANCE), named by the
# descriptor's `conversion_unit` value. `Voltage`/`Angle` fields use a
# different (V_base-anchored) mechanism, not `needs_conversion`+
# `conversion_unit`, so they are not part of this table.
CONVERSION_UNIT_QUANTITY_TYPES = {
    ":mva": {"ActivePower", "ReactivePower", "ApparentPower", "ActivePowerChangeRate"},
    ":ohm": {"Resistance", "Reactance", "Impedance"},
    ":siemens": {"Conductance", "Susceptance"},
}


def load_conversion_unit_families():
    """Map each `conversion_unit` to the set of natural (non-"pu") schema
    `x-unit` strings its quantity family allows, sourced from Core/units.json
    so the mapping cannot drift from the vocabulary independently of it."""
    units_path = os.path.join(REPO_ROOT, "Core", "units.json")
    with open(units_path, encoding="utf-8") as f:
        vocab = json.load(f)
    families = {}
    for conversion_unit, quantity_types in CONVERSION_UNIT_QUANTITY_TYPES.items():
        families[conversion_unit] = {
            entry["unit"]
            for entry in vocab["allowed_units"]
            if entry["quantity_type"] in quantity_types and entry["unit"] != "pu"
        }
    return families


def julia_struct_fields(path, struct_name):
    """Field names of a hand-written Julia struct (lines `name :: Type`)."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    match = re.search(
        rf"struct {struct_name}\b.*?\n(.*?)\nend", text, re.S
    )
    if match is None:
        return None
    fields = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith('"'):
            continue
        if line.startswith("function "):
            break
        field_match = re.match(r"([a-z_][a-zA-Z0-9_]*)\s*::", line)
        if field_match:
            fields.append(field_match.group(1))
    return fields


def _load_hand_written(structs, unresolved, root, table):
    """Add each struct in `table` (name -> Julia source relative to `root`) to
    `structs`, or to `unresolved` when its source file is absent."""
    for name, rel_path in table.items():
        full = os.path.normpath(os.path.join(root, rel_path))
        fields = julia_struct_fields(full, name) if os.path.exists(full) else None
        if fields is None:
            unresolved.add(name)
            print(f"SKIP hand-written struct {name}: {full} not found")
        else:
            structs[name] = fields


def load_psy_structs(psy_path, is_path):
    """Returns (structs, unresolved, defaults, conversions, annotated_types).
    Hand-written structs whose Julia source is absent (partial checkout) land
    in `unresolved` so their schema components are skipped instead of
    reported as spurious MISSING STRUCT. `conversions` carries, for every
    openapi_type-annotated struct only, the needs_conversion field metadata
    (conversion_unit/openapi_unit) check_unit_consistency needs."""
    descriptor_path = os.path.join(
        psy_path, "src", "descriptors", "power_system_structs.json"
    )
    with open(descriptor_path, encoding="utf-8") as f:
        descriptor = json.load(f)
    dynamics_supertypes = derive_dynamics_supertypes(psy_path)
    structs = {}
    defaults = {}
    conversions = {}
    annotated_types = set()
    unresolved = set()
    for entry in descriptor["auto_generated_structs"]:
        if entry.get("supertype", "") in dynamics_supertypes:
            continue
        internal = PSY_INTERNAL.get(entry["struct_name"], set())
        structs[entry["struct_name"]] = [
            field["name"]
            for field in entry.get("fields", [])
            if field["name"] not in internal
        ]
        defaults[entry["struct_name"]] = {
            field["name"]: field["default"]
            for field in entry.get("fields", [])
            if "default" in field
        }
        if "openapi_type" in entry:
            annotated_types.add(entry["struct_name"])
            conversions[entry["struct_name"]] = {
                field["name"]: {
                    "conversion_unit": field.get("conversion_unit"),
                    "openapi_unit": field.get("openapi_unit"),
                }
                for field in entry.get("fields", [])
                if field.get("needs_conversion")
            }
    _load_hand_written(structs, unresolved, psy_path, HAND_WRITTEN)
    _load_hand_written(structs, unresolved, is_path, IS_HAND_WRITTEN)
    return structs, unresolved, defaults, conversions, annotated_types


def load_schema_components():
    """Titled components under Operations/ and Core/ (Dynamics/ and Investments/
    map to PSY dynamics and PSIP respectively, so they are excluded per the
    check's semantics). Sources the file superset from validate_units'
    collect_schema_files so both scripts agree on which schema files exist."""
    components = {}
    defaults = {}
    for path in collect_schema_files():
        if path.relative_to(REPO_ROOT).parts[0] not in ("Operations", "Core"):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        title = doc.get("title")
        if title:
            props = doc.get("properties", {})
            components[title] = {
                name: node if isinstance(node, dict) else {}
                for name, node in props.items()
            }
            defaults[title] = {
                name: node["default"]
                for name, node in props.items()
                if isinstance(node, dict) and "default" in node
            }
    return components, defaults


def explained_psy_only(name, field):
    if field in DROPPED_FIELDS:
        return True
    if field in ASSOCIATION_NORMALIZED.get(name, set()):
        return True
    if name in PLANT_SA_STRUCTS and field.endswith("_map"):
        return True
    return False


def as_number(value):
    """Return value as a float, or None if it is not a scalar number. Booleans
    are treated as non-numeric so a JSON `true`/`false` default is never coerced.
    PSY descriptor defaults are strings ("1.0", "1e8"); schema defaults are typed
    JSON. Only pairs where BOTH sides are numeric are compared."""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def explained_schema_only(name, prop, node):
    if prop == "id":
        return True
    if prop == "reserve_direction" and name in RESERVE_DIRECTION_COMPONENTS:
        return True
    if prop in SCHEMA_AHEAD.get(name, set()):
        return True
    # Unit-basis discriminators are schema-only by design: PSY stores each value
    # in a single basis, the interchange layer records the storage basis per row.
    if node.get("$ref", "").endswith("UnitBasis"):
        return True
    return False


def check_converter_coverage(psy_path, annotated_types):
    """Every hand-written converter's declared registration
    (HAND_WRITTEN_CONVERTERS) must match what actually exists in
    src/openapi/import_handwritten.jl, in both directions, and no type may be
    registered as both generated (openapi_type-annotated) and hand-written.
    Returns the drift count."""
    drifts = 0
    actual = derive_handwritten_converter_types(psy_path)
    if actual is None:
        print(
            f"SKIP converter-coverage check: {HANDWRITTEN_CONVERTERS_REL_PATH} "
            f"not found under {psy_path}"
        )
        return 0
    for name in sorted(HAND_WRITTEN_CONVERTERS - actual):
        print(f"CONVERTER DRIFT missing {name}: registered hand-written, no from_openapi found")
        drifts += 1
    for name in sorted(actual - HAND_WRITTEN_CONVERTERS):
        print(f"CONVERTER DRIFT unregistered {name}: from_openapi found, not registered")
        drifts += 1
    for name in sorted(HAND_WRITTEN_CONVERTERS & annotated_types):
        print(f"CONVERTER DRIFT overlap {name}: both openapi_type-annotated and hand-written")
        drifts += 1
    return drifts


def check_unit_consistency(conversions, components, families):
    """For every openapi_type-annotated PSY struct, every field with
    needs_conversion+conversion_unit must have a schema x-unit consistent
    with it — "pu" when the descriptor's `openapi_unit` key says so,
    otherwise a natural unit in the conversion_unit's quantity family. The
    reverse direction (a schema x-unit of "pu" with no matching openapi_unit
    key) is caught structurally: "pu" is never a member of a natural-unit
    family, so it fails the same branch.
    Returns the drift count; a missing plain x-unit (absent, or only a
    discriminated x-units) is its own drift rather than silently skipped."""
    drifts = 0
    for struct_name, fields in sorted(conversions.items()):
        props = components.get(struct_name)
        if props is None:
            continue  # MISSING SCHEMA/STRUCT already reported for this type
        for field_name, meta in sorted(fields.items()):
            prop = TRANSLITERATION.get(field_name, field_name)
            node = props.get(prop)
            if node is None:
                continue  # FIELD DRIFT already reported for this psy_only field
            x_unit = node.get("x-unit")
            if x_unit is None:
                print(
                    f"UNIT DRIFT {struct_name}.{prop}: conversion_unit="
                    f"{meta['conversion_unit']} but schema has no plain x-unit"
                )
                drifts += 1
                continue
            if meta.get("openapi_unit") == "pu":
                if x_unit != "pu":
                    print(
                        f"UNIT DRIFT {struct_name}.{prop}: openapi_unit=pu x-unit={x_unit}"
                    )
                    drifts += 1
                continue
            natural_units = families.get(meta["conversion_unit"], set())
            if x_unit not in natural_units:
                print(
                    f"UNIT DRIFT {struct_name}.{prop}: conversion_unit="
                    f"{meta['conversion_unit']} x-unit={x_unit}"
                )
                drifts += 1
    return drifts


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--psy-path",
        default=os.path.normpath(os.path.join(REPO_ROOT, "..", "PowerSystems.jl")),
        help="PowerSystems.jl checkout (SKIPs cleanly when absent)",
    )
    parser.add_argument(
        "--is-path",
        default=os.path.normpath(
            os.path.join(REPO_ROOT, "..", "InfrastructureSystems.jl")
        ),
        help="InfrastructureSystems.jl checkout supplying the IS-resident "
             "hand-written structs (each SKIPs cleanly when absent)",
    )
    args = parser.parse_args()

    descriptor = os.path.join(
        args.psy_path, "src", "descriptors", "power_system_structs.json"
    )
    if not os.path.exists(descriptor):
        print(f"SKIP: PSY checkout not found at {args.psy_path}")
        return 0

    psy_structs, unresolved, psy_defaults, conversions, annotated_types = (
        load_psy_structs(args.psy_path, args.is_path)
    )
    components, schema_defaults = load_schema_components()

    missing_schemas = 0
    drifts = 0

    drifts += check_converter_coverage(args.psy_path, annotated_types)
    drifts += check_unit_consistency(
        conversions, components, load_conversion_unit_families()
    )

    for name in sorted(set(psy_structs) - set(components)):
        print(f"MISSING SCHEMA {name}")
        missing_schemas += 1

    for title in sorted(set(components) - set(psy_structs)):
        if title in SCHEMA_ONLY_COMPONENTS or title in unresolved:
            continue
        print(f"MISSING STRUCT {title}")
        drifts += 1

    for name in sorted(set(psy_structs) & set(components)):
        psy_fields = {
            TRANSLITERATION.get(field, field) for field in psy_structs[name]
        }
        props = components[name]
        psy_only = sorted(
            field
            for field in psy_fields - set(props)
            if not explained_psy_only(name, field)
        )
        schema_only = sorted(
            prop
            for prop in set(props) - psy_fields
            if not explained_schema_only(name, prop, props[prop])
        )
        if psy_only or schema_only:
            print(f"FIELD DRIFT {name}: psy_only={psy_only} schema_only={schema_only}")
            drifts += 1

        # Default drift: for every field where BOTH the PSY descriptor and the
        # schema property declare a numeric default, the values must agree
        # (1.0 == 1). Non-numeric or one-sided defaults are not compared.
        psy_field_defaults = psy_defaults.get(name, {})
        schema_prop_defaults = schema_defaults.get(name, {})
        for field, psy_raw in sorted(psy_field_defaults.items()):
            prop = TRANSLITERATION.get(field, field)
            if prop not in schema_prop_defaults:
                continue
            psy_num = as_number(psy_raw)
            schema_num = as_number(schema_prop_defaults[prop])
            if psy_num is None or schema_num is None:
                continue
            if psy_num != schema_num:
                print(
                    f"DEFAULT DRIFT {name}.{prop}: "
                    f"psy={psy_raw} schema={schema_prop_defaults[prop]}"
                )
                drifts += 1

    print(f"SUMMARY: {missing_schemas} missing schemas, {drifts} unexplained drifts")
    return 1 if (missing_schemas + drifts) > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
