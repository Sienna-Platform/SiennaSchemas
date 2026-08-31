#!/usr/bin/env python3
"""Gate: the InfrastructureCore package must carry no power semantics.

Three rules, each a separate failure mode:

1. MEMBERSHIP  -- openapi-infrastructure-core.json names exactly the 20
   definitions the design fixed. A drive-by addition is a layering decision
   and must be an explicit edit here, not a silent one there.
2. DISJOINT    -- openapi-core.json and openapi-infrastructure-core.json
   share no schema name. A definition belongs to exactly one package.
3. CLOSURE     -- nothing reachable from the InfrastructureCore or TimeSeries
   selectors resolves to a definition outside the InfrastructureCore set.
   This is the rule that actually keeps ACBusType and ThermalFuels out of
   what the store and the data layer consume.

Run: python3 scripts/check_layering.py
"""

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The membership fixed by the design. Seventeen definitions inside
# Core/common.json plus three standalone files.
INFRA_MEMBERS = {
    # unit convention
    "UnitSystem",
    # function data -- IS-owned in Julia, and named directly by the store's
    # ElementType (linear_function, quadratic_function, piecewise_*)
    "FunctionData",
    "LinearFunctionData",
    "QuadraticFunctionData",
    "PiecewiseLinearData",
    "PiecewiseStepData",
    "TimeSeriesLinearFunctionData",
    "TimeSeriesQuadraticFunctionData",
    "TimeSeriesPiecewiseLinearData",
    "TimeSeriesPiecewiseStepData",
    # value shapes with no power semantics
    "ComplexNumber",
    "XY_Coords",
    "MinMax",
    "UpDown",
    "InOut",
    "FromTo",
    "FromTo_ToFrom",
    # standalone files
    "GeographicInfo",
    "DataSource",
    "SupplementalAttributeAssociation",
}


def load(name):
    return json.loads((REPO_ROOT / name).read_text())


def schema_names(spec):
    return set(spec["components"]["schemas"])


def iter_refs(node):
    """Yield every $ref string anywhere in a JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from iter_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_refs(value)


def definition_name(ref):
    """The definition a $ref names, or None when it names a whole file."""
    if "#/definitions/" not in ref:
        return None
    return ref.rsplit("/", 1)[-1]


def resolve(path, base):
    return (base.parent / path).resolve()


def closure_definitions(start_files):
    """Every Core/common.json definition reachable from the given files.

    Walks $refs transitively. Only definitions in Core/common.json are
    reported, because that is the one file where generic and power
    definitions physically coexist and so the only place the boundary
    can leak.

    Every ref is carried with the file it came from, which a flat list of
    ref strings cannot express: a bare "#/definitions/X" means something
    different depending on where it was written. In a selector it is a
    self-reference worth nothing; inside Core/common.json it is how a
    generic definition reaches its helpers -- and how one could reach a
    power definition. Dropping same-file refs wholesale would make CLOSURE
    blind to exactly the leak it exists to catch, and it would still pass
    against today's schemas, because every intra-file target currently
    happens to be a member. That is a property of the data, not of the code.
    """
    common = (REPO_ROOT / "Core" / "common.json").resolve()
    common_defs = load("Core/common.json")["definitions"]
    reached = set()
    seen_files = set()
    queue = [(REPO_ROOT / f).resolve() for f in start_files]

    def absorb(name, pending):
        """Record a common.json definition and queue the refs it makes."""
        if name in reached:
            return
        reached.add(name)
        for ref in iter_refs(common_defs.get(name, {})):
            pending.append((ref, common))

    while queue:
        current = queue.pop()
        if current in seen_files or not current.exists():
            continue
        seen_files.add(current)
        doc = json.loads(current.read_text())
        pending = [(ref, current) for ref in iter_refs(doc)]
        while pending:
            ref, origin = pending.pop()
            file_part = ref.split("#")[0]
            name = definition_name(ref)
            if not file_part:
                if origin == common and name:
                    absorb(name, pending)
                continue
            target = resolve(file_part, origin)
            if target == common and name:
                absorb(name, pending)
            else:
                queue.append(target)
    return reached


def main():
    failures = []

    infra = load("openapi-infrastructure-core.json")
    core = load("openapi-core.json")

    # 1. MEMBERSHIP
    declared = schema_names(infra)
    if declared != INFRA_MEMBERS:
        for extra in sorted(declared - INFRA_MEMBERS):
            failures.append(
                f"MEMBERSHIP: {extra!r} is in openapi-infrastructure-core.json "
                f"but not in this gate's INFRA_MEMBERS. Adding a member is a "
                f"layering decision -- edit INFRA_MEMBERS deliberately."
            )
        for missing in sorted(INFRA_MEMBERS - declared):
            failures.append(
                f"MEMBERSHIP: {missing!r} is expected in "
                f"openapi-infrastructure-core.json and is absent."
            )

    # 2. DISJOINT
    for shared in sorted(schema_names(core) & declared):
        failures.append(
            f"DISJOINT: {shared!r} is claimed by both openapi-core.json and "
            f"openapi-infrastructure-core.json. A definition belongs to "
            f"exactly one package."
        )

    # 3. CLOSURE
    reached = closure_definitions(
        ["openapi-infrastructure-core.json", "openapi-timeseries.json"]
    )
    for leaked in sorted(reached - INFRA_MEMBERS):
        failures.append(
            f"CLOSURE: {leaked!r} in Core/common.json is reachable from the "
            f"InfrastructureCore/TimeSeries selectors but is not an "
            f"InfrastructureCore member. The generic layer would inherit a "
            f"power definition."
        )

    if failures:
        for line in failures:
            print(f"FAIL {line}", file=sys.stderr)
        print(f"\n{len(failures)} layering violation(s).", file=sys.stderr)
        return 1

    print(
        f"OK: InfrastructureCore holds {len(INFRA_MEMBERS)} definitions, "
        f"disjoint from the power core, closure clean."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
