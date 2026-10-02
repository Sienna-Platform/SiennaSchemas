#!/usr/bin/env python3
"""Compatibility gate: is every document valid under the base tag still valid now?

Resolves all six domains (plus the two hand-written document schemas) in the
base tree and in the working tree, diffs every named schema, and classifies each
difference as a *feature* (allowed within a compatibility line) or *breaking*.
Any keyword not explicitly listed as a feature is breaking: the gate fails closed.

``info.version`` (V) must agree across the six selectors. V == base version (B):
report only. V != B: fail when V is below the level the changes require.

Stdlib only.
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from refs import (  # noqa: E402
    DOMAINS,
    REPO_ROOT,
    default_name,
    load_json,
    resolved_document,
    target_names,
    _repoint,
)
from schema_version import parse  # noqa: E402

DOCUMENTS = ["Core/SystemDocument.json", "Investments/PortfolioDocument.json"]
COSMETIC = {"description", "title", "examples", "$comment"}
NAME_MAPS = {"properties", "patternProperties", "$defs", "definitions"}
DATA_KEYS = {"enum", "const", "default", "discriminator", "required"}
SKIPPED = {"$defs", "definitions"}
UNIT_KEYS = {"x-unit", "x-units", "x-unit-discriminator", "x-unit-base"}
LOWER_BOUNDS = {"minimum", "exclusiveMinimum", "minLength", "minItems"}
UPPER_BOUNDS = {"maximum", "exclusiveMaximum", "maxLength", "maxItems"}
UNION_KEYS = {"oneOf", "anyOf"}

BREAKING = "breaking"
FEATURE = "feature"


class Findings(list):
    def add(self, kind, where, path, msg):
        self.append((kind, where, path, msg))


class _Names(dict):
    def __missing__(self, target):
        return default_name(target)


def canon(x):
    return json.dumps(x, sort_keys=True)


def strip(x):
    """Drop cosmetic keys at schema positions; map keys are names and stay."""
    if isinstance(x, list):
        return [strip(v) for v in x]
    if not isinstance(x, dict):
        return x
    out = {}
    for k, v in x.items():
        if k in COSMETIC:
            continue
        if k in NAME_MAPS and isinstance(v, dict):
            out[k] = {n: strip(sv) for n, sv in v.items()}
        elif k in DATA_KEYS:
            out[k] = v
        else:
            out[k] = strip(v)
    return out


def type_set(t):
    s = set(t) if isinstance(t, list) else {t}
    if "number" in s:
        s.add("integer")
    return s


def diff_type(b, h, out, where, path):
    bs, hs = type_set(b), type_set(h)
    if bs == hs:
        return
    kind = FEATURE if bs < hs else BREAKING
    verb = "widened" if kind == FEATURE else "narrowed or changed"
    out.add(kind, where, path, f"type {verb}: {canon(b)} -> {canon(h)}")


def diff_bound(key, b, h, out, where, path):
    if b == h:
        return
    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (b, h))
    where_msg = f"{key}: {canon(b)} -> {canon(h)}"
    if b is None or h is None or not numeric:
        relaxed = h is None
    elif key in LOWER_BOUNDS:
        relaxed = h < b
    else:
        relaxed = h > b
    out.add(FEATURE if relaxed else BREAKING, where, f"{path}/{key}",
            f"bound {'relaxed' if relaxed else 'tightened'} ({where_msg})")


def diff_additional(b, h, out, where, path):
    if b == h:
        return
    rank = lambda v: 2 if v is True else (0 if v is False else 1)  # noqa: E731
    if rank(b) == 1 and rank(h) == 1:
        diff_schema(b, h, out, where, f"{path}/additionalProperties")
        return
    kind = FEATURE if rank(h) > rank(b) else BREAKING
    verb = "widened" if kind == FEATURE else "narrowed"
    out.add(kind, where, f"{path}/additionalProperties",
            f"additionalProperties {verb}: {canon(b)} -> {canon(h)}")


def diff_union(key, b, h, out, where, path):
    bc = [canon(strip(x)) for x in b]
    hc = [canon(strip(x)) for x in h]
    rem = [x for x in b if canon(strip(x)) not in hc]
    add = [x for x in h if canon(strip(x)) not in bc]
    if len(rem) == len(add) and rem:
        for i, (rb, ah) in enumerate(zip(rem, add)):
            diff_schema(rb, ah, out, where, f"{path}/{key}[branch {i}]")
        return
    for x in rem:
        out.add(BREAKING, where, f"{path}/{key}", f"branch removed: {canon(x)}")
    for x in add:
        out.add(FEATURE, where, f"{path}/{key}", f"branch added: {canon(x)}")


def diff_mapping(b, h, out, where, path):
    for tag in sorted(set(b) | set(h)):
        mp = f"{path}/discriminator/mapping/{tag}"
        if tag not in h:
            out.add(BREAKING, where, mp, f"mapping entry removed (was {b[tag]})")
        elif tag not in b:
            out.add(FEATURE, where, mp, f"mapping entry added -> {h[tag]}")
        elif b[tag] != h[tag]:
            out.add(BREAKING, where, mp, f"mapping retargeted: {b[tag]} -> {h[tag]}")


def diff_discriminator(b, h, out, where, path):
    if not (isinstance(b, dict) and isinstance(h, dict)):
        out.add(BREAKING, where, f"{path}/discriminator", "discriminator added or removed")
        return
    for key in sorted(set(b) | set(h)):
        if key == "mapping":
            diff_mapping(b.get(key, {}), h.get(key, {}), out, where, path)
        elif b.get(key) != h.get(key):
            out.add(BREAKING, where, f"{path}/discriminator/{key}",
                    f"discriminator.{key} changed: {canon(b.get(key))} -> {canon(h.get(key))}")


def diff_properties(b, h, req_h, out, where, path):
    for name in sorted(set(b) | set(h)):
        pp = f"{path}/properties/{name}"
        if name not in h:
            out.add(BREAKING, where, pp, "property removed")
        elif name not in b:
            if name not in req_h:
                out.add(FEATURE, where, pp, "property added")
        else:
            diff_schema(b[name], h[name], out, where, pp)


def diff_required(b, h, out, where, path):
    for name in sorted(set(h) - set(b)):
        out.add(BREAKING, where, f"{path}/required", f"property added to required: {name}")
    for name in sorted(set(b) - set(h)):
        out.add(FEATURE, where, f"{path}/required", f"property dropped from required: {name}")


def diff_enum(b, h, out, where, path):
    bc = {canon(x) for x in b}
    hc = {canon(x) for x in h}
    for x in sorted(bc - hc):
        out.add(BREAKING, where, f"{path}/enum", f"enum value removed: {x}")
    for x in sorted(hc - bc):
        out.add(FEATURE, where, f"{path}/enum", f"enum value added: {x}")


def diff_schema(b, h, out, where, path=""):
    if not (isinstance(b, dict) and isinstance(h, dict)):
        if canon(b) != canon(h):
            out.add(BREAKING, where, path or "/", "schema changed shape")
        return
    for key in sorted((set(b) | set(h)) - COSMETIC - SKIPPED):
        bv, hv = b.get(key), h.get(key)
        if key in b and key in h and canon(strip({key: bv})) == canon(strip({key: hv})):
            continue
        p = f"{path}/{key}"
        if key == "properties":
            diff_properties(bv or {}, hv or {}, set(h.get("required", [])), out, where, path)
        elif key == "required":
            diff_required(bv or [], hv or [], out, where, path)
        elif key == "type" and key in b and key in h:
            diff_type(bv, hv, out, where, p)
        elif key == "enum" and key in b and key in h:
            diff_enum(bv, hv, out, where, path)
        elif key in LOWER_BOUNDS | UPPER_BOUNDS:
            diff_bound(key, bv, hv, out, where, path)
        elif key == "pattern":
            if hv is None:
                out.add(FEATURE, where, p, f"pattern removed (was {canon(bv)})")
            else:
                out.add(BREAKING, where, p, f"pattern added or changed: {canon(bv)} -> {canon(hv)}")
        elif key == "additionalProperties":
            diff_additional(True if bv is None else bv, True if hv is None else hv,
                            out, where, path)
        elif key in UNION_KEYS and key in b and key in h:
            diff_union(key, bv, hv, out, where, path)
        elif key == "discriminator":
            diff_discriminator(bv, hv, out, where, path)
        elif key == "items" and isinstance(bv, dict) and isinstance(hv, dict):
            diff_schema(bv, hv, out, where, p)
        elif key == "$ref":
            out.add(BREAKING, where, p, f"$ref retargeted: {canon(bv)} -> {canon(hv)}")
        elif key == "default":
            out.add(BREAKING, where, p, f"default changed: {canon(bv)} -> {canon(hv)}")
        elif key in UNIT_KEYS:
            out.add(BREAKING, where, p, f"{key} changed: {canon(bv)} -> {canon(hv)}")
        elif key == "const":
            out.add(BREAKING, where, p, f"const added or changed: {canon(bv)} -> {canon(hv)}")
        else:
            out.add(BREAKING, where, p, f"unclassified keyword changed: {canon(strip({key: bv})[key])} -> {canon(strip({key: hv})[key])}")


def document_schemas(root):
    """Hand-written document schemas, resolved against every domain's names."""
    root = Path(root)
    names = _Names()
    for domain in DOMAINS:
        names.update(target_names(domain, root))
    result = {}
    for rel in DOCUMENTS:
        path = (root / rel).resolve()
        if path.is_file():
            result[f"doc/{Path(rel).stem}"] = _repoint(load_json(path), path, names)
    return result


def collect(root):
    """Every comparable schema under `root`, keyed ``domain/Name``."""
    schemas = {}
    for domain in DOMAINS:
        doc = resolved_document(domain, root)
        for name, body in doc["components"]["schemas"].items():
            schemas[f"{domain}/{name}"] = body
    schemas.update(document_schemas(root))
    return schemas


def versions(root):
    return {d: load_json(Path(root) / f"openapi-{d}.json")["info"]["version"] for d in DOMAINS}


def diff_trees(base_root, head_root):
    base, head = collect(base_root), collect(head_root)
    out = Findings()
    for key in sorted(set(base) | set(head)):
        if key not in head:
            out.add(BREAKING, key, "/", "named schema removed")
        elif key not in base:
            out.add(FEATURE, key, "/", "named schema added")
        else:
            diff_schema(base[key], head[key], out, key)
    return out


def required_version(b, level):
    major, minor, patch, _ = parse(b)
    if level == BREAKING:
        return (0, minor + 1, 0) if major == 0 else (major + 1, 0, 0)
    if level == FEATURE:
        return (0, minor, patch + 1) if major == 0 else (major, minor + 1, 0)
    return (major, minor, patch)


def fmt(v):
    return ".".join(str(x) for x in v)


def extract_base(base, dest):
    archive = subprocess.run(["git", "-C", str(REPO_ROOT), "archive", base],
                             check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)


def default_base():
    return subprocess.run(["git", "-C", str(REPO_ROOT), "describe", "--tags", "--abbrev=0", "--exclude", "*-*"],
                          check=True, capture_output=True, text=True).stdout.strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", help="git ref to compare against (default: latest tag)")
    args = ap.parse_args(argv)
    base = args.base or default_base()

    head_versions = versions(REPO_ROOT)
    if len(set(head_versions.values())) != 1:
        print("FAIL: selectors disagree on info.version:")
        for d, v in head_versions.items():
            print(f"  openapi-{d}.json: {v}")
        return 1
    v = head_versions[DOMAINS[0]]

    with tempfile.TemporaryDirectory() as tmp:
        extract_base(base, tmp)
        b = versions(tmp)[DOMAINS[0]]
        findings = diff_trees(tmp, REPO_ROOT)

    breaking = [f for f in findings if f[0] == BREAKING]
    features = [f for f in findings if f[0] == FEATURE]
    level = BREAKING if breaking else FEATURE if features else None
    required = required_version(b, level)

    for label, group in (("BREAKING", breaking), ("FEATURE", features)):
        print(f"{label} ({len(group)})")
        for _, where, path, msg in group:
            print(f"  {where} {path}: {msg}")
    print(f"base {base} (version {b}); current version {v}; required next version {fmt(required)}")

    vt, bt = parse(v)[:3], parse(b)[:3]
    if v == b:
        print("report only: version not yet bumped")
        return 0
    problems = []
    if vt < bt:
        problems.append(f"version {v} is below base version {b}")
    elif vt < required:
        problems.append(f"changes require at least {fmt(required)}")
    if problems:
        for p in problems:
            print(f"FAIL: {p}")
        return 1
    print(f"OK: version {v} covers the changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
