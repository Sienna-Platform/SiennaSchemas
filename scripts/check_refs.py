#!/usr/bin/env python3
"""Verify every `$ref` and `discriminator.mapping` value actually resolves.

Two passes, because the two artifact shapes resolve differently:

1. Source schemas. A `$ref` is chased across files, or within its own file,
   and its target must exist. A `discriminator.mapping` (or `defaultMapping`)
   value is a reference too, resolved relative to the file that contains it
   exactly like `$ref`, so a mapping inside `Core/common.json` reads
   `#/$defs/X` and one inside a component file reads
   `../../Core/common.json#/$defs/X`. This is what lets every schema file
   stand on its own, and what the bundler relies on to rewrite them.

2. Bundled specs, built in-process (dist/ is gitignored and may not exist).
   A bundle is self-contained, so both `$ref` and `discriminator.mapping`
   must resolve as literal JSON pointers into it, and it must carry no root
   `$defs` block, which the Julia generator's document validator rejects.

Exit 1 and print one line per unresolved target.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bundle_specs import (  # noqa: E402
    DOMAINS,
    BundleError,
    bundle_spec,
    is_external,
    split_ref,
)

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
    """Yield (label, target) for each discriminator reference on `node`."""
    disc = node.get("discriminator")
    if not isinstance(disc, dict):
        return
    mapping = disc.get("mapping")
    if isinstance(mapping, dict):
        for key, target in mapping.items():
            yield f"mapping/{key}", target
    default = disc.get("defaultMapping")
    if isinstance(default, str):
        yield "defaultMapping", default


def check_source_target(file_path, doc, ref, label, errors):
    """Resolve one reference the way a JSON Schema tool would: file part
    relative to the containing file, fragment as a pointer into the target."""
    filepart, fragment = split_ref(ref)
    if filepart:
        target = (file_path.parent / filepart).resolve()
        if not target.exists():
            errors.append(f"{file_path}:{label} -> missing file {filepart}")
            return
        target_doc = load_json(target)
    else:
        target_doc = doc
    try:
        resolve_fragment(target_doc, fragment)
    except KeyError as exc:
        dest = filepart or file_path.name
        errors.append(f"{file_path}:{label} -> {dest}#{fragment} ({exc})")


def check_source(file_path, doc, errors):
    for path, node in find_nodes(doc):
        ref = node.get("$ref")
        if isinstance(ref, str):
            check_source_target(file_path, doc, ref, f"{path} $ref", errors)
        for label, target in mapping_targets(node):
            if not isinstance(target, str):
                errors.append(f"{file_path}:{path}/discriminator/{label} is not a string")
                continue
            check_source_target(
                file_path, doc, target, f"{path}/discriminator/{label}", errors
            )


def check_bundle(domain, errors):
    try:
        bundled = bundle_spec(domain)
    except BundleError as exc:
        errors.append(f"bundle:{domain} cannot be built: {exc}")
        return
    if "$defs" in bundled:
        errors.append(f"bundle:{domain} carries a root $defs block")
    for path, node in find_nodes(bundled):
        ref = node.get("$ref")
        targets = []
        if isinstance(ref, str):
            targets.append((f"{path} $ref", ref))
        for label, target in mapping_targets(node):
            targets.append((f"{path}/discriminator/{label}", target))
        for label, target in targets:
            if is_external(target):
                errors.append(f"bundle:{domain}{label} is still external: {target}")
                continue
            _, fragment = split_ref(target)
            try:
                resolve_fragment(bundled, fragment)
            except KeyError as exc:
                errors.append(f"bundle:{domain}{label} -> {target} ({exc})")


def main():
    errors = []

    source_files = collect_source_files()
    for f in source_files:
        check_source(f, load_json(f), errors)

    for domain in DOMAINS:
        check_bundle(domain, errors)

    if errors:
        print(f"{len(errors)} unresolved $ref/discriminator target(s):")
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
