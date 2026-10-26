#!/usr/bin/env python3
"""Reference rule for the dynamic links in a SystemDocument.

JSON Schema cannot check a reference between two components, so a reader runs
this rule on the decoded document. Each check is one id lookup, so the cost is
linear in the number of components.

* Only a static injection device (a type whose schema declares
  `dynamic_injector`) carries a non-null `dynamic_injector`, and it names a
  dynamic injection.
* Each dynamic injection is named by exactly one static device: none leaves it
  with no bus, two would make one model drive two devices. The two have the
  same `name`.
* Each function block reference on a dynamic injection
  (`excitation_system_dynamics`, `filter`, ...) names a standard model of that
  function block class.
* Each function block is named by exactly one dynamic injection, for the same
  reasons.

Links resolve only when ids are unique across `components`, so a duplicate id
is the only error reported for that document.

`base_power` is data on every row, so a function block, its dynamic injection, and the
static device may record different bases; the ingestion layer reconciles them.

Terms follow the CGMES Dynamics profile (IEC 61970-302): a function block
(`DynamicsFunctionBlock`) is one model component, its function block class is
the abstract parent (`ExcitationSystemDynamics`, ...), and a standard model is
a concrete type (`SEXS`, ...). Two inverter classes take the IEC 61400-27-1
block names that CGMES uses for full converter turbines: `CurrentLimitation`
and `ReferenceFrameRotation`. A class with no CGMES equivalent (the shaft, the
converter, the active and reactive power controls, the inner control, the DC
source, and the filter) keeps the data model library's abstract type name.
Each class is one source directory under `Dynamics/`.

Stdlib only.
"""

from collections import defaultdict
from pathlib import Path

from refs import REPO_ROOT, load_json

GEN = "DynamicGeneratorComponent"
INV = "DynamicInverterComponent"
BLOCK_CLASS_DIRS = {
    "SynchronousMachineDynamics": f"{GEN}/SynchronousMachineDynamics",
    "Shaft": f"{GEN}/Shaft",
    "ExcitationSystemDynamics": f"{GEN}/ExcitationSystemDynamics",
    "TurbineGovernorDynamics": f"{GEN}/TurbineGovernorDynamics",
    "PowerSystemStabilizerDynamics": f"{GEN}/PowerSystemStabilizerDynamics",
    "Converter": f"{INV}/Converter",
    "ActivePowerControl": f"{INV}/ActivePowerControl",
    "ReactivePowerControl": f"{INV}/ReactivePowerControl",
    "InnerControl": f"{INV}/InnerControl",
    "DCSource": f"{INV}/DCSource",
    "ReferenceFrameRotation": f"{INV}/ReferenceFrameRotation",
    "Filter": f"{INV}/Filter",
    "CurrentLimitation": f"{INV}/CurrentLimitation",
}
# dynamic injection type -> function block reference property -> function block class
REFERENCES = {
    "DynamicGenerator": {
        "synchronous_machine_dynamics": "SynchronousMachineDynamics",
        "shaft": "Shaft",
        "excitation_system_dynamics": "ExcitationSystemDynamics",
        "turbine_governor_dynamics": "TurbineGovernorDynamics",
        "power_system_stabilizer_dynamics": "PowerSystemStabilizerDynamics",
    },
    "DynamicInverter": {
        "converter": "Converter",
        "active_power_control": "ActivePowerControl",
        "reactive_power_control": "ReactivePowerControl",
        "inner_control": "InnerControl",
        "dc_source": "DCSource",
        "reference_frame_rotation": "ReferenceFrameRotation",
        "filter": "Filter",
        "current_limitation": "CurrentLimitation",
    },
}


def standard_models(root=REPO_ROOT):
    """Function block class -> the standard model type names in it."""
    dynamics = Path(root) / "Dynamics"
    return {
        block_class: {load_json(p)["title"] for p in (dynamics / d).glob("*.json")}
        for block_class, d in BLOCK_CLASS_DIRS.items()
    }


def static_injectors(root=REPO_ROOT):
    """The component type names whose schema declares `dynamic_injector`."""
    return {
        schema["title"]
        for schema in map(load_json, (Path(root) / "Operations").rglob("*.json"))
        if "dynamic_injector" in schema.get("properties", {})
    }


def _exactly_one(errors, kind, ident, owners, what):
    if len(owners) != 1:
        errors.append(
            f"{kind} {ident}: named by {len(owners)} {what} "
            f"({', '.join(owners) or 'none'}); expected exactly 1"
        )


def link_errors(doc, models_by_class=None, static_kinds=None):
    """Every broken dynamic link in `doc`, as sorted messages."""
    if models_by_class is None:
        models_by_class = standard_models()
    if static_kinds is None:
        static_kinds = static_injectors()
    components = doc["components"]
    owners = defaultdict(list)
    for kind, rows in components.items():
        for row in rows:
            owners[row["id"]].append(kind)
    errors = [
        f"id {ident}: used by {len(kinds)} components ({', '.join(kinds)}); expected exactly 1"
        for ident, kinds in owners.items()
        if len(kinds) > 1
    ]
    if errors:
        return sorted(errors)
    kind_of = {ident: kinds[0] for ident, kinds in owners.items()}

    injectors = defaultdict(list)
    static_name = {}
    for kind, rows in components.items():
        for row in rows:
            target = row.get("dynamic_injector")
            if target is None:
                continue
            if kind not in static_kinds:
                errors.append(f"{kind} {row['id']}: has a dynamic_injector, but is not a static injection device")
            if kind_of.get(target) not in REFERENCES:
                errors.append(
                    f"{kind} {row['id']}: dynamic_injector {target} names "
                    f"{kind_of.get(target, 'no component')}, expected a dynamic injection"
                )
            injectors[target].append(f"{kind} {row['id']}")
            static_name[target] = row.get("name")

    users = defaultdict(list)
    for kind, refs in REFERENCES.items():
        for row in components.get(kind, []):
            _exactly_one(errors, kind, row["id"], injectors[row["id"]], "static devices")
            name = row.get("name")
            if name is None:
                errors.append(f"{kind} {row['id']}: no name")
            elif len(injectors[row["id"]]) == 1 and static_name[row["id"]] != name:
                errors.append(
                    f"{kind} {row['id']}: name {name!r} differs from "
                    f"{injectors[row['id']][0]} name {static_name[row['id']]!r}"
                )
            for prop, block_class in refs.items():
                target = row.get(prop)
                if target is None:
                    continue
                if kind_of.get(target) not in models_by_class[block_class]:
                    errors.append(
                        f"{kind} {row['id']}: {prop} {target} names "
                        f"{kind_of.get(target, 'no component')}, expected a standard model of {block_class}"
                    )
                users[target].append(f"{kind} {row['id']}")

    models = set().union(*models_by_class.values())
    for kind, rows in components.items():
        if kind in models:
            for row in rows:
                _exactly_one(errors, kind, row["id"], users[row["id"]], "dynamic injections")
    return sorted(errors)
