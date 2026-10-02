#!/usr/bin/env python3
"""Validate example time series associations and component instances against their schemas.

The other gates check annotations and $ref structure; none of them validates an
instance. This one does, which is what catches a oneOf matching two branches, a
wrong `required` list, or a discriminator mapping that names the wrong schema.

It also builds the current strict bundles (scripts/build_bundles.py) and checks
the stamped System and Portfolio documents against them, so a bundle regression
fails on the PR instead of at release.

Known limit, recorded rather than papered over: none of the six time series schemas sets
`additionalProperties: false`, because no schema in this repo does. So a property
that a type should not carry -- a `resolution` on a NonSequentialTimeSeries -- is
accepted as an unconstrained extra. `invalid_nonsequential_with_resolution.json`
documents exactly that, and is expected to validate. Closing it would mean
`additionalProperties: false` on all six.
"""

import copy
import json
import pathlib
import sys
import tempfile
import warnings

from build_bundles import build, current_version
from schema_version import classify, message

# `RefResolver` is deprecated in favor of the `referencing` library, but that
# library resolves relative (un-$id'd) refs per-document rather than against a
# directory base URI the way this script needs, so replacing it here would be
# a large rewrite for a test script. Kept deliberately, warning silenced.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from jsonschema import Draft7Validator, Draft202012Validator, RefResolver

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TS_DIR = REPO_ROOT / "TimeSeries"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
BASE_URI = TS_DIR.as_uri() + "/"

# fixture stem -> the schema title it must validate against, and match uniquely
POSITIVE = {
    "single_time_series": "SingleTimeSeries",
    "non_sequential_time_series": "NonSequentialTimeSeries",
    "deterministic": "Deterministic",
    "deterministic_single_time_series": "DeterministicSingleTimeSeries",
    "probabilistic": "Probabilistic",
    "scenarios": "Scenarios",
}

# fixture stem -> whether the wrapper is expected to accept it
NEGATIVE = {
    "invalid_single_missing_resolution": False,
    "invalid_reserved_feature_name": False,
    # See the module docstring: accepted, because additionalProperties is open.
    "invalid_nonsequential_with_resolution": True,
}

# Component fixtures, in tests/fixtures/components: fixture stem ->
# (schema file relative to the repo root, JSON pointer within it or None,
# expected acceptance)
COMPONENT = {
    "ofl_valid": ("Core/common.json", "/$defs/OperationalFlowLimit", True),
    "ofl_negative": ("Core/common.json", "/$defs/OperationalFlowLimit", False),
    "ofl_half": ("Core/common.json", "/$defs/OperationalFlowLimit", False),
    "line_with_ofl": ("Operations/Branch/Line.json", None, True),
    "line_without_ofl": ("Operations/Branch/Line.json", None, True),
}


def load(path):
    with open(path) as fh:
        return json.load(fh)


def validator_for(schema):
    return Draft202012Validator(schema, resolver=RefResolver(BASE_URI, schema))


def check_bundles(failures):
    """Build the current strict bundles and check pass and fail documents."""
    docs = {
        "SystemDocument": ("system_document", "ACBus", "supplemental_attributes"),
        "PortfolioDocument": ("portfolio_document", "Area", "supplemental_attributes"),
    }
    count = 0
    with tempfile.TemporaryDirectory() as tmp:
        version = current_version(REPO_ROOT)
        build(REPO_ROOT, version, tmp)
        for name, (stem, component, attrs) in docs.items():
            validator = Draft7Validator(load(pathlib.Path(tmp) / version / f"{name}.json"))
            base = load(FIXTURE_DIR / "versioning" / f"{stem}.json")

            def mutated(edit):
                doc = copy.deepcopy(base)
                edit(doc)
                return doc

            cases = {
                "stamped document": (lambda d: None, True),
                "ext stays open": (lambda d: d["ext"].update(new_key={"a": 1}), True),
                "unknown row property": (
                    lambda d: d["components"][component][0].update(from_the_future=1),
                    False,
                ),
                "unknown component type": (
                    lambda d: d["components"].update(FutureType=[{"id": 99}]),
                    False,
                ),
                "unknown attribute property": (
                    lambda d: d[attrs][0].update(from_the_future=1),
                    False,
                ),
                "unknown top-level property": (lambda d: d.update(from_the_future=1), False),
            }
            for label, (edit, should_pass) in cases.items():
                count += 1
                accepted = validator.is_valid(mutated(edit))
                if accepted != should_pass:
                    verb = "accepted" if accepted else "rejected"
                    want = "accept" if should_pass else "reject"
                    failures.append(f"{name} bundle, {label}: was {verb}, expected to {want}")
    return count


def main():
    wrapper = load(TS_DIR / "TimeSeriesAssociation.json")
    wrapper_validator = validator_for(wrapper)

    branches = []
    resolver = RefResolver(BASE_URI, wrapper)
    for entry in wrapper["oneOf"]:
        _, sub = resolver.resolve(entry["$ref"])
        branches.append((sub["title"], validator_for(sub)))

    failures = []

    # A schema added to the oneOf with no matching POSITIVE fixture would
    # otherwise pass silently -- every existing fixture still validates, and
    # nothing checks that the wrapper's branch count and the fixture count
    # actually agree.
    if len(wrapper["oneOf"]) != len(POSITIVE):
        failures.append(
            f"TimeSeriesAssociation.json's oneOf has {len(wrapper['oneOf'])} branch(es), "
            f"but POSITIVE lists {len(POSITIVE)} fixture(s) -- add a fixture (and a POSITIVE "
            "entry) for every new branch, or remove the stale one"
        )

    for stem, expected_title in POSITIVE.items():
        instance = load(FIXTURE_DIR / f"{stem}.json")
        hits = [title for title, v in branches if v.is_valid(instance)]
        if hits != [expected_title]:
            failures.append(
                f"{stem}: expected to match exactly [{expected_title}], matched {hits}"
            )
        errors = [e.message for e in wrapper_validator.iter_errors(instance)]
        if errors:
            failures.append(f"{stem}: rejected by TimeSeriesAssociation: {errors[0]}")

    for stem, should_pass in NEGATIVE.items():
        instance = load(FIXTURE_DIR / f"{stem}.json")
        accepted = wrapper_validator.is_valid(instance)
        if accepted != should_pass:
            verb = "accepted" if accepted else "rejected"
            want = "accept" if should_pass else "reject"
            failures.append(f"{stem}: was {verb}, expected the schema to {want} it")

    for stem, (schema_rel, pointer, should_pass) in COMPONENT.items():
        schema_path = REPO_ROOT / schema_rel
        schema = load(schema_path)
        node = schema
        if pointer:
            for part in pointer.split("/"):
                if part:
                    node = node[part]
        v = Draft202012Validator(node, resolver=RefResolver(schema_path.parent.as_uri() + "/", schema))
        accepted = v.is_valid(load(FIXTURE_DIR / "components" / f"{stem}.json"))
        if accepted != should_pass:
            verb = "accepted" if accepted else "rejected"
            want = "accept" if should_pass else "reject"
            failures.append(f"{stem}: was {verb}, expected the schema to {want} it")

    cases = load(FIXTURE_DIR / "versioning" / "cases.json")["cases"]
    texts = {}
    for i, case in enumerate(cases):
        label = f"versioning case {i} (reader {case['reader']}, document {case['document']})"
        if not isinstance(case["document"], dict):
            failures.append(f"{label}: document is not a JSON object")
            continue
        try:
            got = classify(case["reader"], case["document"])
        except (ValueError, TypeError) as e:
            failures.append(f"{label}: classify raised {type(e).__name__}: {e}")
            continue
        if got != case["outcome"]:
            failures.append(f"{label}: classify gave {got}, expected {case['outcome']}")
            continue
        if got in ("upgradable", "current"):
            continue
        d = case["document"].get("schema_version")
        text = message(got, case["reader"], d)
        if "expected_message" in case and text != case["expected_message"]:
            failures.append(f"{label}: message differs from expected_message: {text}")
        encoded = json.dumps(d, separators=(",", ":"), ensure_ascii=False)
        named = {"missing": [], "malformed": [encoded]}.get(got, [d, case["reader"]])
        for value in named:
            if value not in text:
                failures.append(f"{label}: message does not name {value}: {text}")
        if got == "incompatible":
            cause = "dev builds" if "dev builds" in text else "line"
            # first-match order: a prerelease on either side wins over a line crossing
            want = "dev builds" if "-" in d or "-" in case["reader"] else "line"
            if cause != want:
                failures.append(f"{label}: message is the {cause!r} text, expected {want!r}")
            if cause == "line" and "(line " not in text:
                failures.append(f"{label}: line message names no lines: {text}")
            texts[cause] = text
    for cause in ("dev builds", "line"):
        if cause not in texts:
            failures.append(f"no incompatible vector exercises the {cause!r} message")

    bundle_checks = check_bundles(failures)

    if failures:
        print(f"FAIL: {len(failures)} fixture check(s) failed:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    total = len(POSITIVE) + len(NEGATIVE) + len(COMPONENT)
    print(
        f"OK: {total} fixture(s) validate as expected; {len(cases)} versioning case(s) match; "
        f"{bundle_checks} strict-bundle check(s) pass."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
