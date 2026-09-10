#!/usr/bin/env python3
"""Verify every `$ref` and `discriminator.mapping` value actually resolves.

Two passes, because the two artifact shapes resolve differently:

1. Source schemas. A `$ref` is chased across files, or within its own file,
   and its target must exist. A `discriminator.mapping` value is a bare
   fragment with no file part, and an unbundled component file has no
   namespace to resolve it against (Operations/StaticInjection/
   ThermalStandard.json carries a discriminator but has neither a
   `$defs` nor a `components` key), so only its tail name is checked --
   against every Core/common.json definition and every aggregate's
   `components.schemas` key. Which prefix an unbundled file should spell is
   the bundler's call, not this script's.

2. Bundled specs, built in-process (dist/ is gitignored and may not exist).
   A bundle is self-contained, so both `$ref` and `discriminator.mapping`
   must resolve as literal JSON pointers into it.

Exit 1 and print one line per unresolved target.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bundle_specs import COMMON_FILES, DOMAINS, bundle_spec, is_external, split_ref  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["Core", "Operations", "Investments", "Dynamics", "TimeSeries"]

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
    """Every schema JSON file, plus the aggregates that own a `components.schemas`
    namespace. Excludes units.json, which is not a schema."""
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
    """Every name a discriminator.mapping value may legitimately point at."""
    names = set()
    for rel in COMMON_FILES:
        names |= set(load_json((REPO_ROOT / rel).resolve()).get("$defs", {}).keys())
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


def mapping_targets(node):
    """Yield (key, target) for each discriminator.mapping entry on `node`."""
    disc = node.get("discriminator")
    if not isinstance(disc, dict):
        return
    mapping = disc.get("mapping")
    if isinstance(mapping, dict):
        yield from mapping.items()


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
        for key, target in mapping_targets(node):
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
        for key, target in mapping_targets(node):
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
