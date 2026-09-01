#!/usr/bin/env python3
"""Gate: the InfrastructureCore package must carry no power semantics.

Four rules, each a separate failure mode:

1. MEMBERSHIP    -- openapi-infrastructure-core.json names exactly the 20
   definitions the design fixed. A drive-by addition is a layering decision
   and must be an explicit edit here, not a silent one there.
2. DISJOINT      -- openapi-core.json and openapi-infrastructure-core.json
   share no schema name. A definition belongs to exactly one package.
3. CLOSURE       -- nothing reachable from the InfrastructureCore or
   TimeSeries selectors resolves to a Core/common.json definition outside
   the InfrastructureCore set. This is the rule that actually keeps
   ACBusType and ThermalFuels out of what the store and the data layer
   consume.
4. REACHABILITY  -- every file actually reached while walking that closure
   is on an explicit allowlist (Core/common.json, the three standalone
   member files, everything under TimeSeries/, and the two selectors
   themselves). CLOSURE alone only judges definitions absorbed out of
   Core/common.json; a $ref retargeted at, or added inside, any other file
   in the closure walks straight through CLOSURE unjudged. REACHABILITY is
   what catches that file itself.

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
    """The definition a $ref names, or None when it names a whole file.

    A deep JSON pointer past a definition
    (`#/$defs/CostCurve/properties/value_curve`) is judged by its
    first path token -- the definition it points *into* -- not discarded.
    Returning None there would let a ref reach a non-member definition's
    guts (e.g. a power definition's property) without ever being judged as
    reaching that definition: a false negative on the very boundary this
    gate exists to hold. `.../MinMax/properties/min` is judged as
    `MinMax`, a real member, so no false failure results.
    """
    marker = "#/$defs/"
    if marker not in ref:
        return None
    tail = ref.split(marker, 1)[1]
    return tail.split("/", 1)[0]


def resolve(path, base):
    return (base.parent / path).resolve()


def display_path(path):
    """REPO_ROOT-relative path when possible, absolute otherwise.

    A $ref may resolve outside the repo entirely (an absolute path, or a
    long enough chain of `..`). That must still produce a clean, named
    failure -- not an uncaught ValueError from `.relative_to`.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _time_series_files():
    """Resolved TimeSeries/*.json files, excluding a symlink whose target
    resolves outside TimeSeries/ -- otherwise a symlink dropped in the
    directory launders its target onto the allowlist for every other ref
    in the repo, not just refs from inside TimeSeries/."""
    ts_dir = (REPO_ROOT / "TimeSeries").resolve()
    files = set()
    for entry in ts_dir.glob("*.json"):
        resolved = entry.resolve()
        try:
            resolved.relative_to(ts_dir)
        except ValueError:
            continue
        files.add(resolved)
    return files


# Files a closure walk from the InfrastructureCore/TimeSeries selectors may
# legitimately reach: Core/common.json (member and power definitions
# physically coexist there, judged by CLOSURE), the three standalone member
# files, everything under TimeSeries/, and the two selectors themselves.
# REACHABILITY fails on anything else the walk touches.
ALLOWED_FILES = {
    (REPO_ROOT / "Core" / "common.json").resolve(),
    (REPO_ROOT / "Core" / "SupplementalAttributes" / "GeographicInfo.json").resolve(),
    (REPO_ROOT / "Core" / "SupplementalAttributes" / "DataSource.json").resolve(),
    (
        REPO_ROOT / "Core" / "Associations" / "SupplementalAttributeAssociation.json"
    ).resolve(),
    (REPO_ROOT / "openapi-infrastructure-core.json").resolve(),
    (REPO_ROOT / "openapi-timeseries.json").resolve(),
} | _time_series_files()


def closure_definitions(start_files):
    """Walk every $ref reachable from the given files.

    Returns (reached, seen_files, missing_refs, reached_via):
      - reached: every Core/common.json definition absorbed along the way.
        Only Core/common.json definitions are reported here, because that
        is the one file where generic and power definitions physically
        coexist and so the only place CLOSURE can judge by definition name.
        REACHABILITY judges every other file the walk touches by identity.
      - seen_files: every file the walk actually opened.
      - missing_refs: (ref, origin, target) for every ref whose target does
        not exist on disk, instead of silently skipping it.
      - reached_via: for every non-start file, the (ref, origin) that first
        queued it, so a REACHABILITY failure can name how the file was
        reached rather than just that it was.

    Every ref is carried with the file it came from, which a flat list of
    ref strings cannot express: a bare "#/$defs/X" means something
    different depending on where it was written. In a selector it is a
    self-reference worth nothing; inside Core/common.json it is how a
    generic definition reaches its helpers -- and how one could reach a
    power definition. Dropping same-file refs wholesale would make CLOSURE
    blind to exactly the leak it exists to catch, and it would still pass
    against today's schemas, because every intra-file target currently
    happens to be a member. That is a property of the data, not of the code.
    """
    common = (REPO_ROOT / "Core" / "common.json").resolve()
    common_defs = load("Core/common.json")["$defs"]
    reached = set()
    seen_files = set()
    missing_refs = []
    reached_via = {}
    queue = [((REPO_ROOT / f).resolve(), None, None) for f in start_files]

    def absorb(name, pending):
        """Record a common.json definition and queue the refs it makes."""
        if name in reached:
            return
        reached.add(name)
        for ref in iter_refs(common_defs.get(name, {})):
            pending.append((ref, common))

    while queue:
        current, ref, origin = queue.pop()
        if current in seen_files:
            continue
        if not current.exists():
            missing_refs.append((ref, origin, current))
            continue
        seen_files.add(current)
        doc = json.loads(current.read_text())
        pending = [(r, current) for r in iter_refs(doc)]
        while pending:
            r, o = pending.pop()
            file_part = r.split("#")[0]
            name = definition_name(r)
            if not file_part:
                if o == common and name:
                    absorb(name, pending)
                continue
            target = resolve(file_part, o)
            if target == common:
                # common.json itself must never be queued as a plain file
                # (that would flatten every same-file bare ref inside it,
                # including between power definitions that have nothing to
                # do with the ref that got us here). A whole-file ref
                # (name is None) is not nothing, though: it is a $ref to
                # every definition common.json holds, so absorb all of
                # them -- silently passing here would let a whole-file ref
                # smuggle the entire power core through unjudged.
                if name:
                    absorb(name, pending)
                else:
                    for common_name in common_defs:
                        absorb(common_name, pending)
            else:
                if target not in reached_via:
                    reached_via[target] = (r, o)
                queue.append((target, r, o))
    return reached, seen_files, missing_refs, reached_via


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
    reached, seen_files, missing_refs, reached_via = closure_definitions(
        ["openapi-infrastructure-core.json", "openapi-timeseries.json"]
    )
    for leaked in sorted(reached - INFRA_MEMBERS):
        failures.append(
            f"CLOSURE: {leaked!r} in Core/common.json is reachable from the "
            f"InfrastructureCore/TimeSeries selectors but is not an "
            f"InfrastructureCore member. The generic layer would inherit a "
            f"power definition."
        )
    for ref, origin, target in missing_refs:
        origin_rel = display_path(origin) if origin else "a selector"
        failures.append(
            f"CLOSURE: {ref!r} referenced from {origin_rel} does not "
            f"resolve to an existing file ({target}). A dangling ref "
            f"cannot be judged for layering and must not be skipped."
        )

    # 4. REACHABILITY
    for extra in sorted(seen_files - ALLOWED_FILES, key=lambda p: str(p)):
        rel = display_path(extra)
        via_ref, via_origin = reached_via.get(extra, (None, None))
        if via_ref is None:
            how = "reached directly as a selector"
        else:
            how = f"reached via {via_ref!r} from {display_path(via_origin)}"
        failures.append(
            f"REACHABILITY: {rel} is reachable from the "
            f"InfrastructureCore/TimeSeries selectors ({how}) but is not "
            f"on the allowlist of files InfrastructureCore may draw from "
            f"(Core/common.json, the three standalone member files, "
            f"TimeSeries/*, and the two selectors). A $ref pointing here "
            f"-- retargeted or newly added -- would ship power semantics "
            f"inside InfrastructureCore unjudged by CLOSURE."
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
