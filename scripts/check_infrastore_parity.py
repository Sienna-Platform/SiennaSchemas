#!/usr/bin/env python3
"""Compare the time series schemas' field names against infrastore's catalog row.

Reads the CREATE TABLE for `time_series_associations` out of infrastore's
metadata/schema.rs and diffs its columns against the union of properties across
the six per-type schemas. Every difference must be one of the deliberate ones in
ALLOWED_SCHEMA_ONLY / ALLOWED_INFRASTORE_ONLY; anything else is drift.

Skips cleanly (exit 0, with a SKIP line) when the infrastore checkout is absent,
matching how check_psy_parity.py handles a missing PowerSystems.jl.
"""

import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TS_DIR = REPO_ROOT / "TimeSeries"
DEFAULT_INFRASTORE = REPO_ROOT.parent / "time-series-store"
DDL_REL = pathlib.Path("crates/infrastore-core/src/metadata/schema.rs")

SCHEMAS = [
    "SingleTimeSeries",
    "NonSequentialTimeSeries",
    "Deterministic",
    "DeterministicSingleTimeSeries",
    "Probabilistic",
    "Scenarios",
]

# Present here, absent from infrastore's catalog -- each deliberate.
ALLOWED_SCHEMA_ONLY = {
    # infrastore IS the store, so it never needs to locate itself.
    "address",
    # On infrastore's Scenarios struct but in no catalog column: it reads the
    # count off the stored array's leading dim, and JSON carries no array.
    "scenario_count",
    # The catalog spells this percentiles_json (a JSON string); the Rust struct
    # and this schema carry a real array.
    "percentiles",
    # TimeSeriesMetadata carries this inline as a map (see metadata.rs's
    # `pub features: Features`), matching this schema. The catalog row has no
    # plain `features` column -- it only has the content-addressed
    # `features_hash`; see that entry in ALLOWED_INFRASTORE_ONLY.
    "features",
}

# Present in infrastore's catalog, absent here -- each deliberate.
ALLOWED_INFRASTORE_ONLY = {
    # Content addresses for bytes this layer does not carry.
    "data_hash",
    "timestamps_hash",
    # features is inlined as a map here, matching the TimeSeriesMetadata struct,
    # rather than carried as the hash of a shared feature_sets row.
    "features_hash",
    # See ALLOWED_SCHEMA_ONLY["percentiles"].
    "percentiles_json",
}


def infrastore_columns(ddl_path):
    text = ddl_path.read_text()
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS time_series_associations \((.*?)\n\);",
        text,
        re.DOTALL,
    )
    if match is None:
        raise SystemExit(f"could not find the time_series_associations DDL in {ddl_path}")
    columns = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        name = stripped.split()[0]
        if name.isidentifier():
            columns.append(name)
    return set(columns)


def schema_properties():
    props = set()
    for name in SCHEMAS:
        schema = json.loads((TS_DIR / f"{name}.json").read_text())
        props |= set(schema["properties"])
    return props


def main():
    root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INFRASTORE
    ddl_path = root / DDL_REL
    if not ddl_path.exists():
        print(f"SKIP: no infrastore checkout at {root}; pass its path as argv[1].")
        return 0

    catalog = infrastore_columns(ddl_path)
    ours = schema_properties()

    schema_only = (ours - catalog) - ALLOWED_SCHEMA_ONLY
    infrastore_only = (catalog - ours) - ALLOWED_INFRASTORE_ONLY

    # An allowlist entry is a claim that the name is actually present on its
    # side. Subtracting it out (above) only ever hides drift; it never checks
    # the claim. A field deleted from the schemas (or the catalog) simply
    # vanishes from the `ours - catalog` (or `catalog - ours`) set and nothing
    # notices -- so assert presence too, naming exactly which allowlisted name
    # went missing.
    missing_schema_only = sorted(name for name in ALLOWED_SCHEMA_ONLY if name not in ours)
    missing_infrastore_only = sorted(
        name for name in ALLOWED_INFRASTORE_ONLY if name not in catalog
    )

    if schema_only or infrastore_only or missing_schema_only or missing_infrastore_only:
        print("FAIL: undeclared drift against infrastore's catalog row:", file=sys.stderr)
        for name in sorted(schema_only):
            print(f"  - {name}: in the schemas, not in the catalog", file=sys.stderr)
        for name in sorted(infrastore_only):
            print(f"  - {name}: in the catalog, not in the schemas", file=sys.stderr)
        for name in missing_schema_only:
            print(
                f"  - {name}: declared in ALLOWED_SCHEMA_ONLY but no longer in any schema",
                file=sys.stderr,
            )
        for name in missing_infrastore_only:
            print(
                f"  - {name}: declared in ALLOWED_INFRASTORE_ONLY but no longer in the catalog",
                file=sys.stderr,
            )
        return 1

    shared = len(ours & catalog)
    observed_schema_only = len(ALLOWED_SCHEMA_ONLY & ours)
    observed_infrastore_only = len(ALLOWED_INFRASTORE_ONLY & catalog)
    print(
        f"OK: {shared} field(s) agree with infrastore's catalog row; "
        f"{observed_schema_only} declared schema-only, "
        f"{observed_infrastore_only} declared infrastore-only."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
