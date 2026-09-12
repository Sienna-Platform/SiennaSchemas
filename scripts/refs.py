#!/usr/bin/env python3
"""Reference resolution shared by every script that walks the schema tree.

A reference is a ``$ref``, a ``discriminator.mapping`` value, or a
``discriminator.defaultMapping`` value. All three are URI references resolved
relative to the file that carries them, so all three go through
:func:`resolve_ref_path` and land on the same *target*: a ``(resolved file
path, fragment)`` pair, where the fragment is ``''`` for a whole-file schema
and ``/$defs/<Name>`` for a definition.

Each ``openapi-<domain>.json`` selector maps a component name onto one target.
The generators name their output after those keys, so a schema a domain reaches
but does not declare has no name to generate under --- which is what
:func:`undeclared_targets` reports and what ``check_refs.py`` gates on.

Stdlib only.
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOMAINS = [
    "infrastructure-core",
    "core",
    "operations",
    "investments",
    "dynamics",
    "timeseries",
]

# common.json (and any other multiply-referenced file) is parsed once per
# process. Keyed by resolved absolute path.
_file_cache = {}


class RefError(ValueError):
    """A selector or reference shape these helpers refuse to guess about."""


def load_json(path):
    key = str(Path(path).resolve())
    if key not in _file_cache:
        with open(key) as fh:
            _file_cache[key] = json.load(fh)
    return _file_cache[key]


def split_ref(ref):
    """Split a reference into (filepart, fragment). filepart '' means same document."""
    if "#" in ref:
        return tuple(ref.split("#", 1))
    return ref, ""


def is_external(ref):
    filepart, _ = split_ref(ref)
    return filepart != ""


def resolve_fragment(doc, fragment):
    """Follow a JSON-pointer fragment (leading '/') into doc."""
    node = doc
    if fragment in ("", "/"):
        return node
    for token in fragment.strip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        else:
            raise KeyError(f"fragment token '{token}' not found in {fragment}")
    return node


def resolve_ref_path(base_path, ref):
    """Split a reference into a target, the file part resolved relative to the
    document carrying it."""
    filepart, fragment = split_ref(ref)
    path = (base_path.parent / filepart).resolve() if filepart else base_path
    if fragment == "/":
        fragment = ""
    if fragment and not fragment.startswith("/"):
        raise RefError(f"{base_path.name}: unsupported reference {ref!r}")
    if not path.is_file():
        raise RefError(f"{base_path.name}: {ref!r} names a missing file")
    return (path, fragment)


def default_name(target):
    """The component name a target claims when a selector does not name it: the
    file stem for a whole-file schema, the key for a ``$defs`` entry."""
    path, fragment = target
    if fragment == "":
        return path.stem
    tokens = fragment.strip("/").split("/")
    if len(tokens) == 2 and tokens[0] == "$defs":
        return tokens[1]
    raise RefError(
        f"{path.name}#{fragment}: only whole-file schemas and $defs entries can be "
        "published as components"
    )


def mapping_refs(node):
    """Yield (label, value) for each discriminator reference on `node`."""
    discriminator = node.get("discriminator")
    if not isinstance(discriminator, dict):
        return
    mapping = discriminator.get("mapping")
    if isinstance(mapping, dict):
        for tag, value in mapping.items():
            yield f"discriminator/mapping/{tag}", value
    default = discriminator.get("defaultMapping")
    if default is not None:
        yield "discriminator/defaultMapping", default


def walk_refs(node, path=""):
    """Yield (label, value) for every reference anywhere under `node`."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref is not None:
            yield f"{path}/$ref", ref
        for label, value in mapping_refs(node):
            yield f"{path}/{label}", value
        for key, value in node.items():
            if key != "discriminator":
                yield from walk_refs(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk_refs(value, f"{path}[{index}]")


def selector_path(domain):
    return REPO_ROOT / f"openapi-{domain}.json"


def selector_entries(domain):
    """The domain's declared components as ``name -> target``.

    Every entry must be a bare external ``$ref``: the selector selects, it does
    not define. A name claimed by two targets, or a target claimed under two
    names, is an error rather than something to pick a winner for.
    """
    path = selector_path(domain)
    schemas = load_json(path).get("components", {}).get("schemas", {})
    entries = {}
    seen = {}
    for name, entry in schemas.items():
        if not (
            isinstance(entry, dict)
            and set(entry) == {"$ref"}
            and is_external(entry["$ref"])
        ):
            raise RefError(
                f"{path.name}: components.schemas.{name} must be a bare external "
                "$ref; the selector only selects, it does not define"
            )
        target = resolve_ref_path(path, entry["$ref"])
        if target in seen:
            raise RefError(
                f"{path.name}: {target[0].name}#{target[1]} is published as both "
                f"{seen[target]!r} and {name!r}"
            )
        seen[target] = name
        entries[name] = target
    return entries


def definitions(domain):
    """The domain's declared components as ``name -> schema body``, each body read
    from the file that authors it, with its references left as written."""
    result = {}
    for name, (path, fragment) in selector_entries(domain).items():
        result[name] = resolve_fragment(load_json(path), fragment)
    return result


def target_names(domain):
    """``target -> component name`` for the domain, the inverse of
    :func:`selector_entries`. Resolving a reference to a type name is a lookup
    here."""
    return {target: name for name, target in selector_entries(domain).items()}


def _repoint(node, base_path, names):
    """Copy `node` with every reference rewritten to `#/components/schemas/<name>`."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "$ref":
                out[key] = "#/components/schemas/" + names[resolve_ref_path(base_path, value)]
            elif key == "discriminator" and isinstance(value, dict):
                out[key] = dict(value)
                mapping = value.get("mapping")
                if isinstance(mapping, dict):
                    out[key]["mapping"] = {
                        tag: "#/components/schemas/"
                        + names[resolve_ref_path(base_path, target)]
                        for tag, target in mapping.items()
                    }
                default = value.get("defaultMapping")
                if isinstance(default, str):
                    out[key]["defaultMapping"] = (
                        "#/components/schemas/" + names[resolve_ref_path(base_path, default)]
                    )
            else:
                out[key] = _repoint(value, base_path, names)
        return out
    if isinstance(node, list):
        return [_repoint(value, base_path, names) for value in node]
    return node


def resolved_document(domain):
    """The domain as one self-contained document, in memory.

    Every declared schema under ``components.schemas``, each reference repointed
    at the name it resolves to. Nothing is inlined and no body is reshaped: keys
    beside a ``$ref`` stay where they are.

    Renderers need this flat view --- a Markdown page cannot follow a relative
    file path --- but code generation does not: both toolchains resolve the
    schema tree themselves, and read the selectors directly. Requires the
    selector to declare everything it reaches, which ``check_refs.py`` gates.
    """
    names = target_names(domain)
    doc = dict(load_json(selector_path(domain)))
    components = dict(doc.get("components", {}))
    components["schemas"] = {
        name: _repoint(resolve_fragment(load_json(path), fragment), path, names)
        for name, (path, fragment) in selector_entries(domain).items()
    }
    doc["components"] = components
    return doc


def undeclared_targets(domain):
    """Targets the domain reaches but does not declare, as ``target -> [label]``.

    Walks out from the declared components and follows every reference across
    files, exactly as a JSON Schema tool does. A non-empty result means the
    generators have no name for those schemas and will invent one per reference
    site, silently duplicating a shared type.
    """
    declared = set(selector_entries(domain).values())
    missing = {}
    queue = [(target, target) for target in declared]
    visited = set(declared)
    while queue:
        target, origin = queue.pop(0)
        path, fragment = target
        body = resolve_fragment(load_json(path), fragment)
        for label, ref in walk_refs(body):
            if not isinstance(ref, str):
                raise RefError(f"{path.name}#{fragment}{label}: reference is not a string")
            reached = resolve_ref_path(path, ref)
            if reached not in declared:
                missing.setdefault(reached, []).append(
                    f"{origin[0].name}#{origin[1]}{label}"
                )
            if reached not in visited:
                visited.add(reached)
                queue.append((reached, reached))
    return missing
