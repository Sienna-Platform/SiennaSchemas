#!/usr/bin/env python3
"""Verify every `$ref` and `discriminator.mapping` value resolves, and that each
selector declares everything it reaches.

Two passes:

1. Source schemas. A `$ref` is chased across files, or within its own file, and
   its target must exist. A `discriminator.mapping` (or `defaultMapping`) value
   is a reference too, resolved relative to the file that contains it exactly
   like `$ref`, so a mapping inside `Core/common.json` reads `#/$defs/X`, one
   inside a component file reads `../../Core/common.json#/$defs/X`, and one
   naming a whole sibling file reads `SingleTimeSeries.json#`. This is what lets
   every schema file stand on its own.

2. Selectors. Each `openapi-<domain>.json` must be a well-formed selection ---
   every entry a bare external `$ref`, no name or target claimed twice --- and
   must declare every schema the domain reaches. The generators name their
   output after `components.schemas` keys, so a reached-but-undeclared schema
   has no name to generate under: both toolchains invent one per reference site
   and a shared type silently becomes several.

Exit 1 and print one line per problem.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from refs import (  # noqa: E402
    DOMAINS,
    REPO_ROOT,
    RefError,
    default_name,
    load_json,
    resolve_fragment,
    resolve_ref_path,
    selector_entries,
    selector_path,
    undeclared_targets,
    walk_refs,
)

SCAN_DIRS = ["Core", "Operations", "Investments", "Dynamics", "TimeSeries"]


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
        files.append(selector_path(domain))
    return files


def check_source(file_path, errors):
    for label, ref in walk_refs(load_json(file_path)):
        if not isinstance(ref, str):
            errors.append(f"{file_path.name}:{label} is not a string")
            continue
        try:
            target, fragment = resolve_ref_path(file_path, ref)
        except RefError as exc:
            errors.append(f"{file_path.name}:{label} -> {exc}")
            continue
        try:
            resolve_fragment(load_json(target), fragment)
        except KeyError as exc:
            errors.append(f"{file_path.name}:{label} -> {target.name}#{fragment} ({exc})")


def check_selector(domain, errors):
    try:
        selector_entries(domain)
        missing = undeclared_targets(domain)
    except RefError as exc:
        errors.append(f"openapi-{domain}.json: {exc}")
        return
    for target, labels in sorted(missing.items(), key=lambda item: str(item[0])):
        try:
            name = default_name(target)
        except RefError as exc:
            errors.append(f"openapi-{domain}.json: {exc}")
            continue
        rel = target[0].relative_to(REPO_ROOT).as_posix()
        ref = rel + (f"#{target[1]}" if target[1] else "")
        errors.append(
            f'openapi-{domain}.json: reaches {ref} but does not declare it; add '
            f'"{name}": {{"$ref": "{ref}"}} (reached from {labels[0]})'
        )


def main():
    errors = []

    source_files = collect_source_files()
    for f in source_files:
        check_source(f, errors)

    for domain in DOMAINS:
        check_selector(domain, errors)

    if errors:
        print(f"{len(errors)} problem(s):")
        for e in errors:
            print(f"  {e}")
        return 1

    print(
        f"OK: 0 dangling targets across {len(source_files)} source file(s); "
        f"{len(DOMAINS)} selector(s) declare everything they reach."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
