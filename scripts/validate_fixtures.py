#!/usr/bin/env python3
"""Validate example time series association instances against their schemas.

The other gates check annotations and $ref structure; none of them validates an
instance. This one does, which is what catches a oneOf matching two branches, a
wrong `required` list, or a discriminator mapping that names the wrong schema.

Known limit, recorded rather than papered over: none of the six schemas sets
`additionalProperties: false`, because no schema in this repo does. So a property
that a type should not carry -- a `resolution` on a NonSequentialTimeSeries -- is
accepted as an unconstrained extra. `invalid_nonsequential_with_resolution.json`
documents exactly that, and is expected to validate. Closing it would mean
`additionalProperties: false` on all six.
"""

import json
import pathlib
import sys
import warnings

# `RefResolver` is deprecated in favor of the `referencing` library, but that
# library resolves relative (un-$id'd) refs per-document rather than against a
# directory base URI the way this script needs, so replacing it here would be
# a large rewrite for a test script. Kept deliberately, warning silenced.
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    from jsonschema import Draft202012Validator, RefResolver

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


def load(path):
    with open(path) as fh:
        return json.load(fh)


def validator_for(schema):
    return Draft202012Validator(schema, resolver=RefResolver(BASE_URI, schema))


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

    if failures:
        print(f"FAIL: {len(failures)} fixture check(s) failed:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    total = len(POSITIVE) + len(NEGATIVE)
    print(f"OK: {total} fixture(s) validate as expected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
