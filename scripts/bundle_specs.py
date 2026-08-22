#!/usr/bin/env python3
"""Bundle the five openapi-<domain>.json specs into self-contained specs.

For each ``openapi-<domain>.json`` at the repo root this resolves every
external-file ``$ref`` (a ``$ref`` whose value names a file, i.e. does not start
with ``#/``) and writes ``dist/openapi-<domain>-bundled.json``.

Why this exists
---------------
Under JSON Schema draft-07 and OpenAPI 3.0 any keyword placed **as a sibling of
``$ref``** is ignored: the ``$ref`` replaces the whole object. Most ``x-unit*``
annotations in this repo sit next to a ``$ref`` (a property that is a ``MinMax``,
a ``ReservoirDataType``-discriminated field, ...), so downstream draft-07/OpenAPI
codegen drops them silently. Bundling resolves the external refs and **merges the
sibling annotations into the resolved referent** so nothing is lost.

Resolution rules
----------------
* External-file ``$ref`` (value contains a file part, e.g.
  ``Operations/StaticInjection/ThermalStandard.json`` or
  ``../../Core/common.json#/definitions/MinMax``) is resolved to the referenced
  file+fragment and inlined.
* When the referring node carries sibling keys next to ``$ref`` those siblings
  are merged onto a copy of the resolved referent. **Sibling keys win over
  referent keys on conflict** (so a property's own ``description`` and ``x-unit``
  override the shared definition's).
* ``Core/common.json#/definitions/<Name>`` targets are hoisted once into a
  top-level ``definitions`` block of the bundle. A ref to such a definition that
  has **no** siblings is rewritten to the internal ``#/definitions/<Name>``.
  A ref that **does** carry siblings is inlined in place (a merged copy) because
  the merged object is unique to that use site.
* Refs already internal to the spec (``#/...``) are left untouched -- draft-07
  tooling handles those.

Determinism
-----------
Output uses stable insertion order: keys are emitted in the order they are first
seen while walking (spec order, then referent order), and the hoisted
``definitions`` block is emitted in sorted key order. ``json.dump`` is called
with ``ensure_ascii=False`` and a trailing newline. Given identical inputs the
bytes are identical, which ``--check`` relies on.

Usage
-----
    python scripts/bundle_specs.py            # write dist/*-bundled.json
    python scripts/bundle_specs.py --check     # exit non-zero if dist/ is stale

Stdlib only.
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"
DOMAINS = ["core", "operations", "investments", "dynamics", "timeseries"]

# Files whose top-level `definitions` block is addressed by other schemas via
# a two-token `<file>#/definitions/<Name>` external ref, and so is hoisted
# into a bundle's own `definitions` rather than left as a dangling pointer.
COMMON_FILES = ["Core/common.json", "TimeSeries/common.json"]

# Shared across all five domain bundles: common.json (and any multiply-$ref'd
# file) is parsed from disk once. Keyed by resolved absolute path.
_file_cache = {}


def load_json(path):
    key = str(Path(path).resolve())
    if key not in _file_cache:
        with open(path) as fh:
            _file_cache[key] = json.load(fh)
    return _file_cache[key]


def split_ref(ref):
    """Split a $ref into (filepart, fragment). filepart '' means same document."""
    if "#" in ref:
        filepart, fragment = ref.split("#", 1)
    else:
        filepart, fragment = ref, ""
    return filepart, fragment


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


class Bundler:
    """Bundles one spec. Hoists common-file (Core/common.json,
    TimeSeries/common.json, ...) definitions into `definitions`.

    "Common file" is not a fixed path: any file addressed via a two-token
    `<file>#/definitions/<Name>` external ref, whose own top-level
    `definitions` actually holds `<Name>`, is treated as one. An internal
    `#/definitions/<Name>` ref resolves against *whichever* file is currently
    being walked, per plain JSON-pointer semantics -- that file is what
    `source_path` names below.
    """

    def __init__(self):
        # Names of common-file definitions pulled into the bundle's
        # `definitions`, mapped to their rewritten body.
        self.hoisted = {}
        # Resolved paths of the known common files. Membership in this set --
        # not merely "the target has a #/definitions/<Name> shape" -- is what
        # gates hoisting: any file can coincidentally have a `definitions`
        # block, but only a file listed in COMMON_FILES is meant to be one.
        self._common_paths = {(REPO_ROOT / rel).resolve() for rel in COMMON_FILES}
        # name -> resolved source path, for every definition any known common
        # file exposes. Used only to resolve discriminator.mapping targets,
        # which are spelled '#/components/schemas/<Name>' by convention and
        # need to be told apart from a genuine components.schemas entry --
        # unlike a real $ref, that lookup is not scoped to one walked file.
        self._common_owner = {}
        for common_path in self._common_paths:
            doc = load_json(common_path)
            for name in doc.get("definitions", {}):
                self._common_owner[name] = common_path

    def _hoist_common_definition(self, name, source_path):
        """Ensure a common-file definition is inlined into the bundle definitions.

        Resolves its own internal defn->defn refs to bundle-internal
        #/definitions refs (recursively hoisting the targets)."""
        if name in self.hoisted:
            return
        doc = load_json(source_path)
        if name not in doc.get("definitions", {}):
            raise KeyError(f"{source_path} has no definition '{name}'")
        # Placeholder to break cycles.
        self.hoisted[name] = None
        body = doc["definitions"][name]
        self.hoisted[name] = self._rewrite_common_internal(body, source_path)

    def _rewrite_discriminator_mapping(self, discriminator):
        """Repoint mapping targets at hoisted common-file definitions.

        Source schemas spell every target '#/components/schemas/<Name>', which
        dangles for a common-file definition: hoisting puts those in the
        bundle's top-level `definitions`, not `components.schemas`. A target
        naming something else (an actual components.schemas entry) is left
        untouched."""
        mapping = discriminator.get("mapping")
        if not isinstance(mapping, dict):
            return discriminator
        rewritten = dict(discriminator)
        new_mapping = {}
        for key, target in mapping.items():
            _, fragment = split_ref(target)
            name = fragment.rsplit("/", 1)[-1]
            source_path = self._common_owner.get(name)
            if source_path is not None:
                self._hoist_common_definition(name, source_path)
                new_mapping[key] = f"#/definitions/{name}"
            else:
                new_mapping[key] = target
        rewritten["mapping"] = new_mapping
        return rewritten

    def _rewrite_common_internal(self, node, source_path):
        """Within a common file's content, turn '#/definitions/X' refs into
        bundle '#/definitions/X' refs (same spelling) and hoist X from
        source_path. No external refs exist inside a common file, so only
        internal ones are handled here."""
        if isinstance(node, dict):
            if "$ref" in node and not is_external(node["$ref"]):
                _, fragment = split_ref(node["$ref"])
                tokens = fragment.strip("/").split("/")
                if len(tokens) == 2 and tokens[0] == "definitions":
                    self._hoist_common_definition(tokens[1], source_path)
                # Merge any siblings (rare inside common) onto a rewritten copy.
                out = {}
                for k, v in node.items():
                    out[k] = (
                        v if k == "$ref" else self._rewrite_common_internal(v, source_path)
                    )
                return out
            out = {}
            for k, v in node.items():
                if k == "discriminator":
                    out[k] = self._rewrite_discriminator_mapping(v)
                else:
                    out[k] = self._rewrite_common_internal(v, source_path)
            return out
        if isinstance(node, list):
            return [self._rewrite_common_internal(v, source_path) for v in node]
        return node

    def _resolve_external(self, ref, base_path):
        """Return (resolved_content, target_path, fragment, common_name).

        common_name is the definition name if this targets a common file's
        own definitions (a two-token `definitions/<Name>` fragment that the
        target file actually defines), else None."""
        filepart, fragment = split_ref(ref)
        target = (base_path.parent / filepart).resolve()
        doc = load_json(target)
        content = resolve_fragment(doc, fragment)
        common_name = None
        tokens = fragment.strip("/").split("/")
        if (
            target in self._common_paths
            and len(tokens) == 2
            and tokens[0] == "definitions"
            and tokens[1] in doc.get("definitions", {})
        ):
            common_name = tokens[1]
        return content, target, fragment, common_name

    def walk(self, node, base_path):
        """Recursively bundle a node, resolving external refs relative to
        base_path (the file the node currently lives in)."""
        if isinstance(node, dict):
            if "$ref" in node and not is_external(node["$ref"]):
                # Internal ref: resolves within base_path's own document, per
                # JSON-pointer semantics. Only hoist when base_path is itself
                # a known common file -- an internal ref inside an ordinary
                # schema file has no #/definitions namespace to hoist from.
                if base_path in self._common_paths:
                    _, fragment = split_ref(node["$ref"])
                    tokens = fragment.strip("/").split("/")
                    if len(tokens) == 2 and tokens[0] == "definitions":
                        self._hoist_common_definition(tokens[1], base_path)
                return {
                    k: (v if k == "$ref" else self.walk(v, base_path))
                    for k, v in node.items()
                }
            if "$ref" in node and is_external(node["$ref"]):
                siblings = {k: v for k, v in node.items() if k != "$ref"}
                content, target, _, common_name = self._resolve_external(
                    node["$ref"], base_path
                )
                if common_name is not None and not siblings:
                    # No siblings: hoist and point to internal definition.
                    self._hoist_common_definition(common_name, target)
                    return {"$ref": f"#/definitions/{common_name}"}
                # Inline the resolved content (deep-resolved in its own file
                # context), then merge siblings on top (siblings win).
                resolved = self.walk(content, target)
                merged = {}
                if isinstance(resolved, dict):
                    merged.update(resolved)
                else:
                    # Non-dict referent with siblings cannot merge; siblings win
                    # only makes sense for object referents. Keep resolved.
                    if siblings:
                        merged["allOf"] = [resolved]
                    else:
                        return resolved
                for k, v in siblings.items():
                    merged[k] = self.walk(v, base_path)
                return merged
            # Ordinary dict: recurse, preserving key order.
            out = {}
            for k, v in node.items():
                if k == "discriminator":
                    out[k] = self._rewrite_discriminator_mapping(v)
                else:
                    out[k] = self.walk(v, base_path)
            return out
        if isinstance(node, list):
            return [self.walk(v, base_path) for v in node]
        return node

    def bundle(self, spec_path):
        spec = load_json(spec_path)
        bundled = self.walk(spec, spec_path)
        if self.hoisted:
            defs = {name: self.hoisted[name] for name in sorted(self.hoisted)}
            bundled["definitions"] = defs
        return bundled


def bundle_spec(domain):
    spec_path = REPO_ROOT / f"openapi-{domain}.json"
    return Bundler().bundle(spec_path)


def serialize(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def cmd_write():
    DIST_DIR.mkdir(exist_ok=True)
    for domain in DOMAINS:
        out = DIST_DIR / f"openapi-{domain}-bundled.json"
        out.write_text(serialize(bundle_spec(domain)))
        print(f"wrote {out.relative_to(REPO_ROOT)}")
    return 0


def cmd_check():
    stale = []
    for domain in DOMAINS:
        out = DIST_DIR / f"openapi-{domain}-bundled.json"
        fresh = serialize(bundle_spec(domain))
        if not out.exists():
            stale.append(f"{out.relative_to(REPO_ROOT)} (missing)")
            continue
        if out.read_text() != fresh:
            stale.append(f"{out.relative_to(REPO_ROOT)} (stale)")
    if stale:
        print("Bundled specs are missing or stale:")
        for s in stale:
            print(f"  {s}")
        print("Run: python scripts/bundle_specs.py")
        return 1
    print(f"OK: {len(DOMAINS)} bundled spec(s) up to date in dist/.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if dist/ outputs are missing or stale.",
    )
    args = parser.parse_args()
    if args.check:
        return cmd_check()
    return cmd_write()


if __name__ == "__main__":
    sys.exit(main())
