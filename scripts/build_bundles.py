#!/usr/bin/env python3
"""Build strict, self-contained validation bundles for one release.

Writes ``<out>/<version>/SystemDocument.json`` and ``PortfolioDocument.json``.
Each is one draft-07 root schema with every reachable schema inlined under
``definitions`` and every ``$ref`` local, so any binding's stock validator can use
it. A document that passes is readable by the strictest binding (the ones that
reject unknown keys), which is what a ``source``-version save has to guarantee.

Strictness rules:

* An object schema that carries its own ``properties`` and declares no
  ``additionalProperties`` gets ``additionalProperties: false``. Explicitly open
  maps (``ext``, ``MinMaxByKey``, ...) keep theirs.
* Schemas inside ``allOf``, ``if``, ``then``, ``else`` and ``not`` only add
  constraints to a sibling that already declares the properties; closing them
  would reject those siblings, so they are never closed. A schema that is closed
  while composing inline ``allOf``/``oneOf``/``anyOf`` branches must own every
  property those branches mention, or the build fails rather than guess.
* ``components`` becomes ``properties`` keyed by each component type name (the
  schema ``title``) and is closed: an unknown type name is rejected.
  ``supplemental_attributes`` items become ``anyOf`` over the release's
  attribute schemas.
* Which schemas are components or attributes follows the source directory, as
  below. Dynamics schemas appear in neither document.

OpenAPI-only and vendor keywords (``discriminator``, ``x-*``, ``$schema`` below
the root) are dropped: strict validators such as ajv reject unknown keywords.

Input is a tree root and a version, ``--tag vX.Y.Z`` (extracts the tag), or
``--line`` (every release tag in the current compatibility line up to the current
version, plus the working tree). Output is deterministic.

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
    REPO_ROOT,
    _repoint,
    load_json,
    selector_entries,
    resolve_fragment,
)
from schema_version import line, parse  # noqa: E402

DRAFT = "http://json-schema.org/draft-07/schema#"

# document -> (file, domains whose schemas it reaches, component dirs, attribute dirs)
DOCUMENTS = {
    "SystemDocument": (
        "Core/SystemDocument.json",
        ["infrastructure-core", "core", "operations", "timeseries"],
        [
            "Core/Topology",
            "Operations/Branch",
            "Operations/Market",
            "Operations/Service",
            "Operations/StaticInjection",
        ],
        ["Core/SupplementalAttributes", "Operations/SupplementalAttributes"],
    ),
    "PortfolioDocument": (
        "Investments/PortfolioDocument.json",
        ["infrastructure-core", "core", "investments", "timeseries"],
        ["Core/Topology", "Investments/Technologies", "Investments/Requirements"],
        ["Core/SupplementalAttributes", "Investments/SupplementalAttributes"],
    ),
}

# Hold constraints beside a sibling that declares the properties.
CONSTRAINT = {"allOf", "if", "then", "else", "not"}
# Keys whose values are data, not schemas.
DATA = {"enum", "const", "default", "examples", "required", "type"}
NAME_MAPS = {"properties", "patternProperties"}
BRANCHES = ("allOf", "oneOf", "anyOf")


def _local(ref):
    return ref.replace("#/components/schemas/", "#/definitions/")


def _branch_properties(node):
    names = set()
    for key in BRANCHES:
        for branch in node.get(key, []):
            if isinstance(branch, dict):
                names.update(branch.get("properties", {}))
    return names


def effective_additional(node):
    """The additionalProperties a strict bundle gives `node` (before constraint context)."""
    if "additionalProperties" in node:
        return node["additionalProperties"]
    return not (node.get("properties") and "$ref" not in node)


def strict(node, where, constraint=False):
    """Copy a schema with vendor keys dropped, refs localized, objects closed."""
    if isinstance(node, list):
        return [strict(v, where, constraint) for v in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in ("$schema", "discriminator") or key.startswith("x-"):
            continue
        if key == "$ref":
            out[key] = _local(value)
        elif key in DATA:
            out[key] = value
        elif key in NAME_MAPS:
            out[key] = {n: strict(v, f"{where}/{n}") for n, v in value.items()}
        elif key in CONSTRAINT:
            out[key] = strict(value, f"{where}/{key}", True)
        elif key in ("oneOf", "anyOf"):
            out[key] = [strict(v, f"{where}/{key}[{i}]") for i, v in enumerate(value)]
        else:
            out[key] = strict(value, f"{where}/{key}")
    own = node.get("properties")
    if (
        not constraint
        and "additionalProperties" not in node
        and effective_additional(node) is False
    ):
        extra = _branch_properties(node) - set(own)
        if extra:
            raise ValueError(
                f"{where}: cannot close; composed branches add {sorted(extra)} "
                "beyond the schema's own properties"
            )
        out["additionalProperties"] = False
    return out


def _merged(root, domains):
    """Selector targets of `domains` as ``target -> name`` and ``name -> body``."""
    names, raw, source = {}, {}, {}
    for domain in domains:
        for name, target in selector_entries(domain, root).items():
            if names.setdefault(target, name) != name:
                raise ValueError(f"{target[0].name} is published as {names[target]!r} and {name!r}")
            if source.setdefault(name, target) != target:
                raise ValueError(f"{name!r} is published from two different targets")
            path, fragment = target
            body = resolve_fragment(load_json(path), fragment)
            if name in raw and raw[name][1] != body:
                raise ValueError(f"{name!r} is published by two domains with different bodies")
            raw[name] = (path, body)
    bodies = {n: _repoint(body, path, names) for n, (path, body) in raw.items()}
    return names, bodies


def _names_under(root, names, dirs):
    root = Path(root).resolve()
    result = []
    for (path, fragment), name in names.items():
        if not fragment and any(path.parent == root / d for d in dirs):
            result.append(name)
    return sorted(result)


def _reachable(bodies, seeds):
    prefix = "#/definitions/"
    seen, queue = set(), list(seeds)

    def refs_of(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    yield v
                elif k not in DATA:
                    yield from refs_of(list(v.values()) if k in NAME_MAPS else v)
        elif isinstance(node, list):
            for v in node:
                yield from refs_of(v)

    while queue:
        node = queue.pop()
        for ref in refs_of(node):
            name = ref[len(prefix):]
            if ref.startswith(prefix) and name not in seen:
                seen.add(name)
                queue.append(bodies[name])
    return seen


def build_document(root, document, version):
    file, domains, component_dirs, attribute_dirs = DOCUMENTS[document]
    root = Path(root).resolve()
    names, schemas = _merged(root, domains)
    doc_path = root / file
    doc = _repoint(load_json(doc_path), doc_path, names)

    components = _names_under(root, names, component_dirs)
    attributes = _names_under(root, names, attribute_dirs)
    for name in components + attributes:
        title = schemas[name].get("title")
        if title != name:
            raise ValueError(f"{name}: schema title {title!r} is not the type name writers use")

    props = doc["properties"]
    props["components"] = {
        "description": props["components"]["description"],
        "type": "object",
        "properties": {
            n: {"type": "array", "items": {"$ref": f"#/components/schemas/{n}"}}
            for n in components
        },
        "additionalProperties": False,
    }
    props["supplemental_attributes"]["items"] = {
        "anyOf": [{"$ref": f"#/components/schemas/{n}"} for n in attributes]
    }

    bundle = strict(doc, document)
    bundle["$schema"] = DRAFT
    bundle["title"] = document
    definitions = {n: strict(b, n) for n, b in schemas.items()}
    keep = _reachable(definitions, [bundle])
    bundle["definitions"] = {n: definitions[n] for n in sorted(keep)}
    bundle["$comment"] = f"Strict validation bundle for schema release {version}."
    return bundle


def build(root, version, out):
    dest = Path(out) / version
    dest.mkdir(parents=True, exist_ok=True)
    for document in DOCUMENTS:
        bundle = build_document(root, document, version)
        (dest / f"{document}.json").write_text(
            json.dumps(bundle, indent=2, sort_keys=True) + "\n"
        )
    print(f"built {dest}")


def current_version(root):
    return load_json(Path(root) / "openapi-core.json")["info"]["version"]


def release_tags():
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "tag", "--list", "v*"],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    tags = []
    for t in out:
        try:
            parse(t[1:])
        except ValueError:
            print(f"skipping non-release tag {t}", file=sys.stderr)
        else:
            tags.append(t)
    return sorted(tags, key=lambda t: parse(t[1:])[:3])


def build_tag(tag, out):
    archive = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "archive", tag], check=True, capture_output=True
    ).stdout
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["tar", "-x", "-C", tmp], input=archive, check=True)
        found = current_version(tmp)
        if found != tag[1:]:
            raise ValueError(f"tag {tag} carries info.version {found}")
        build(tmp, tag[1:], out)


def build_line(out):
    current = current_version(REPO_ROOT)
    for tag in release_tags():
        version = tag[1:]
        pv = parse(version)
        if pv[3] is None and line(version) == line(current) and pv[:3] < parse(current)[:3]:
            build_tag(tag, out)
    build(REPO_ROOT, current, out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(REPO_ROOT / "bundles"))
    ap.add_argument("--root", help="tree root to build from (needs --version)")
    ap.add_argument("--version")
    ap.add_argument("--tag", help="release tag to extract and build, e.g. v0.1.0")
    ap.add_argument("--line", action="store_true",
                    help="build every release in the current compatibility line, plus this tree")
    args = ap.parse_args(argv)
    if args.line:
        build_line(args.out)
    elif args.tag:
        build_tag(args.tag, args.out)
    elif args.root and args.version:
        build(args.root, args.version, args.out)
    else:
        ap.error("give --line, --tag, or --root with --version")
    return 0


if __name__ == "__main__":
    sys.exit(main())
