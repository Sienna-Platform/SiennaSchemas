#!/usr/bin/env python3
"""Verify every `$ref` and `discriminator.mapping` value actually resolves.

Two passes, because "resolves" means something different for the two artifact
shapes this repo produces:

1. Source schemas (Core/, Operations/, Investments/, Dynamics/, the four
   openapi-<domain>.json aggregates). These are split across many files by
   design -- that split, and the sibling-annotation loss it causes under
   naive $ref resolution, is exactly what scripts/bundle_specs.py exists to
   fix (see its module docstring). A `$ref` here is checked by chasing it
   across files (external) or within its own file (internal) and confirming
   the target exists. A `discriminator.mapping` value is a bare `#/...`
   fragment with no file part, so literal same-file JSON-pointer resolution
   does not apply to it consistently across this repo's own component files
   (e.g. Operations/StaticInjection/ThermalStandard.json has neither a
   `definitions` nor a `components` top-level key, yet legitimately carries
   a discriminator). Instead each mapping value's tail name is checked
   against the full universe of names any bundle could resolve it to: every
   Core/common.json definition name, plus every domain aggregate's top-level
   `components.schemas` name. This is the check that catches the tracked
   defect class (a mapping target renamed to a prefix that exists nowhere)
   without requiring this script to re-litigate which prefix is "correct"
   for an unbundled file -- that call belongs to the bundler.

2. Bundled specs (scripts/bundle_specs.py's output for the four domains,
   built in-process here rather than read from disk -- dist/ is gitignored
   and may not exist locally). Each bundle is a single, self-contained JSON
   document with no remaining external refs, so both `$ref` and
   `discriminator.mapping` are checked by literal JSON-pointer resolution
   against that one document. This is the strict check, and the one the
   tracked defect actually broke: bundle_specs.py rewrote `$ref` targets
   during hoisting but left `discriminator.mapping` pointing at a prefix
   (`#/components/schemas/<Name>`) the bundle never populates for hoisted
   Core/common.json definitions.

Usage:
    python3 scripts/check_refs.py

Exit 1 and print one line per unresolved target if either pass finds one.
Stdlib only (does not need the bundled files to already exist on disk).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bundle_specs import DOMAINS, bundle_spec, is_external, split_ref  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMON_PATH = (REPO_ROOT / "Core" / "common.json").resolve()
SCAN_DIRS = ["Core", "Operations", "Investments", "Dynamics"]

_doc_cache = {}


def load_json(path):
    key = str(Path(path).resolve())
    if key not in _doc_cache:
        with open(path) as fh:
            _doc_cache[key] = json.load(fh)
    return _doc_cache[key]


def resolve_fragment(doc, fragment):
    node = doc
    if fragment in ("", "/"):
        return node
    for token in fragment.strip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        else:
            raise KeyError(token)
    return node


def collect_source_files():
    """Every schema JSON file plus the four openapi-<domain>.json aggregates.

    Mirrors validate_units.py's SCAN_DIRS convention (excludes units.json,
    not a schema) and additionally includes the aggregates themselves, since
    those are where a top-level `components.schemas` name space is defined.
    """
    files = []
    for d in SCAN_DIRS:
        for p in sorted((REPO_ROOT / d).rglob("*.json")):
            if p.name.startswith("openapi") or p == (REPO_ROOT / "Core" / "units.json"):
                continue
            files.append(p)
    for domain in DOMAINS:
        files.append(REPO_ROOT / f"openapi-{domain}.json")
    return files


def build_name_registry():
    """Every schema name a discriminator.mapping value could legitimately
    name: Core/common.json's definitions, and each domain aggregate's
    top-level components.schemas keys."""
    names = set(load_json(COMMON_PATH).get("definitions", {}).keys())
    for domain in DOMAINS:
        spec = load_json(REPO_ROOT / f"openapi-{domain}.json")
        names |= set(spec.get("components", {}).get("schemas", {}).keys())
    return names


def find_nodes(doc, path=""):
    """Yield (path, dict) for every dict node in doc, depth-first."""
    if isinstance(doc, dict):
        yield path, doc
        for k, v in doc.items():
            yield from find_nodes(v, f"{path}/{k}")
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            yield from find_nodes(v, f"{path}[{i}]")


def check_source_refs(file_path, doc, errors):
    for path, node in find_nodes(doc):
        ref = node.get("$ref")
        if isinstance(ref, str):
            filepart, fragment = split_ref(ref)
            if filepart:
                target = (file_path.parent / filepart).resolve()
                if not target.exists():
                    errors.append(f"{file_path}:{path} $ref -> missing file {filepart}")
                    continue
                target_doc = load_json(target)
            else:
                target_doc = doc
            try:
                resolve_fragment(target_doc, fragment)
            except KeyError as exc:
                dest = filepart or file_path.name
                errors.append(f"{file_path}:{path} $ref -> {dest}#{fragment} ({exc})")


def check_source_discriminators(file_path, doc, names, errors):
    for path, node in find_nodes(doc):
        disc = node.get("discriminator")
        if not isinstance(disc, dict):
            continue
        mapping = disc.get("mapping")
        if not isinstance(mapping, dict):
            continue
        for key, target in mapping.items():
            _, fragment = split_ref(target)
            name = fragment.rsplit("/", 1)[-1]
            if name not in names:
                errors.append(
                    f"{file_path}:{path}/discriminator/mapping/{key} -> "
                    f"'{name}' names no known schema or Core/common.json definition"
                )


def check_bundle(domain, errors):
    bundled = bundle_spec(domain)
    for path, node in find_nodes(bundled):
        ref = node.get("$ref")
        if isinstance(ref, str):
            if is_external(ref):
                errors.append(f"bundle:{domain}{path} $ref is still external: {ref}")
                continue
            _, fragment = split_ref(ref)
            try:
                resolve_fragment(bundled, fragment)
            except KeyError as exc:
                errors.append(f"bundle:{domain}{path} $ref -> {ref} ({exc})")
        disc = node.get("discriminator")
        if not isinstance(disc, dict):
            continue
        mapping = disc.get("mapping")
        if not isinstance(mapping, dict):
            continue
        for key, target in mapping.items():
            if is_external(target):
                errors.append(
                    f"bundle:{domain}{path}/discriminator/mapping/{key} "
                    f"is still external: {target}"
                )
                continue
            _, fragment = split_ref(target)
            try:
                resolve_fragment(bundled, fragment)
            except KeyError as exc:
                errors.append(
                    f"bundle:{domain}{path}/discriminator/mapping/{key} -> "
                    f"{target} ({exc})"
                )


def main():
    errors = []

    names = build_name_registry()
    source_files = collect_source_files()
    for f in source_files:
        doc = load_json(f)
        check_source_refs(f, doc, errors)
        check_source_discriminators(f, doc, names, errors)

    for domain in DOMAINS:
        check_bundle(domain, errors)

    if errors:
        print(f"{len(errors)} unresolved $ref/discriminator.mapping target(s):")
        for e in errors:
            print(f"  {e}")
        return 1

    print(
        f"OK: 0 dangling targets across {len(source_files)} source file(s) "
        f"and {len(DOMAINS)} bundled spec(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
