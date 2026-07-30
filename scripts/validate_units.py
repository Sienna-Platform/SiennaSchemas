#!/usr/bin/env python3
"""Validate the x-unit annotation family across the Sienna schemas.

Runs over every schema JSON file in Core/, Operations/, Investments/, and
Dynamics/ (excluding Core/units.json itself and the openapi-*.json specs) and
enforces:

  1. Every schema validates against the JSON Schema draft-07 meta-schema.
  2. Every x-unit / x-units value is a unit string in Core/units.json
     allowed_units, or the literal "pu".
  3. Every x-unit-base and x-unit-discriminator names an existing sibling
     property in the same object's "properties".
  4. Every x-units key set exactly equals the enum of the property named by the
     sibling x-unit-discriminator. A $ref discriminator is resolved into
     Core/common.json (e.g. ReservoirDataType); a boolean discriminator has the
     effective enum {"true", "false"}.
  5. No "descriptor" keys anywhere; no "type": null anywhere.
  6. Any property literally named "unit" or "units" typed "string" must have a
     description mentioning units.json or the unit vocabulary.

Description channel (extra modes):
  --fix-descriptions    Rewrite every annotated property's description so it
                        ends with the canonical "Units: ..." sentence
                        (idempotent: replaces a stale one, never duplicates).
  --check-descriptions  Exit non-zero if any annotated property's description
                        does not end with its canonical sentence (CI check).

Sentence format (see docs/UNIT_ANNOTATIONS.md):
  plain x-unit    "Units: <x-unit>."
  discriminated   "Units: per <discriminator> — <VAL>: <unit>, ... ."

Stdlib + jsonschema only. Prints one line per failure (file, JSON path, rule,
got/expected) and exits non-zero if any failure is found.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import jsonschema
from jsonschema import Draft7Validator

REPO_ROOT = Path(__file__).resolve().parent.parent
UNITS_JSON = REPO_ROOT / "Core" / "units.json"
SCAN_DIRS = ["Core", "Operations", "Investments", "Dynamics"]

_ref_doc_cache = {}


class Failure:
    def __init__(self, file, path, rule, got, expected):
        self.file = file
        self.path = path
        self.rule = rule
        self.got = got
        self.expected = expected

    def __str__(self):
        return (
            f"FAIL [{self.rule}] {self.file}\n"
            f"     path:     {self.path}\n"
            f"     got:      {self.got}\n"
            f"     expected: {self.expected}"
        )


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def load_json_cached(path):
    """load_json keyed by resolved absolute path; re-parses each file only once."""
    key = str(Path(path).resolve())
    if key not in _ref_doc_cache:
        _ref_doc_cache[key] = load_json(path)
    return _ref_doc_cache[key]


def collect_schema_files():
    files = []
    for d in SCAN_DIRS:
        for p in sorted((REPO_ROOT / d).rglob("*.json")):
            name = p.name
            if name.startswith("openapi"):
                continue
            if p == UNITS_JSON:
                continue
            files.append(p)
    return files


def load_allowed_units():
    units = load_json(UNITS_JSON)
    allowed = {a["unit"] for a in units["allowed_units"]}
    allowed.add("pu")
    return allowed


# Optional sibling SiennaGridDB checkout carrying the DB-owned
# column -> (quantity_type, unit) conventions. When present, it lets rule 2
# tighten the flat vocabulary check into a (quantity_type, unit) pairing check
# for any schema property whose name matches a registered DB column.
COLUMN_CONVENTIONS = (
    REPO_ROOT / ".." / "SiennaGridDB" / "schema" / "column_conventions.json"
)


def load_quantity_units():
    """Map quantity_type -> set of units allowed for it (from units.json)."""
    units = load_json(UNITS_JSON)
    q2u = {}
    for a in units["allowed_units"]:
        q2u.setdefault(a["quantity_type"], set()).add(a["unit"])
    return q2u


def load_column_allowed_units():
    """Map DB column name -> set of units the registry allows for that column's
    quantity_type(s), sourced from the sibling SiennaGridDB
    column_conventions.json crossed with units.json. Returns {} when the sibling
    checkout is absent (the tighter pairing check is then simply skipped)."""
    path = COLUMN_CONVENTIONS.resolve()
    if not path.exists():
        return {}
    q2u = load_quantity_units()
    conventions = load_json(path).get("conventions", [])
    col_quantities = {}
    for entry in conventions:
        column = entry.get("column")
        quantity = entry.get("quantity_type")
        if column is None or quantity is None:
            continue
        col_quantities.setdefault(column, set()).add(quantity)
    col_allowed = {}
    for column, quantities in col_quantities.items():
        allowed = set()
        for quantity in quantities:
            allowed |= q2u.get(quantity, set())
        col_allowed[column] = allowed
    return col_allowed


def resolve_ref_enum(ref, source_path):
    """Resolve a local-file $ref to a definitions entry and return its enum, or None."""
    if "#/" not in ref:
        return None
    filepart, fragment = ref.split("#", 1)
    if filepart == "":
        target = source_path
    else:
        target = (source_path.parent / filepart).resolve()
    if not target.exists():
        return None
    doc = load_json_cached(target)
    node = doc
    for token in fragment.strip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        else:
            return None
    if isinstance(node, dict) and "enum" in node:
        return list(node["enum"])
    return None


def discriminator_enum(disc_prop_schema, source_path):
    """Return the effective enum-value string set of a discriminator property, or None."""
    if not isinstance(disc_prop_schema, dict):
        return None
    if "enum" in disc_prop_schema:
        return {str(v) for v in disc_prop_schema["enum"]}
    if disc_prop_schema.get("type") == "boolean":
        return {"true", "false"}
    if "$ref" in disc_prop_schema:
        enum = resolve_ref_enum(disc_prop_schema["$ref"], source_path)
        if enum is not None:
            return {str(v) for v in enum}
    return None


def validate_x_units_map(x_units, path, enclosing_props, source_path,
                         source_file, failures, allowed_units):
    """Validate an ``x-units`` map.

    Each value is either a unit string (leaf) or a nested discriminator object
    ``{"x-unit-discriminator": <sibling>, "x-units": {...}}`` for a
    multi-dimensional unit (e.g. a VSC setpoint whose quantity depends on the
    control mode and whose basis then depends on a unit-basis sibling). Nested
    objects are validated recursively: the nested discriminator must name an
    existing sibling whose enum equals the nested ``x-units`` keys, and the
    nested leaves must be vocabulary units.
    """
    for key, val in x_units.items():
        subpath = f"{path}/{key}"
        if isinstance(val, str):
            if val not in allowed_units:
                failures.append(
                    Failure(source_file, subpath, "x-unit-vocabulary",
                            val, "a unit in Core/units.json allowed_units or 'pu'")
                )
        elif isinstance(val, dict):
            nested_disc = val.get("x-unit-discriminator")
            nested_units = val.get("x-units")
            if nested_disc is None or not isinstance(nested_units, dict):
                failures.append(
                    Failure(source_file, subpath, "x-units-nested-shape",
                            "nested value",
                            "a unit string or a nested "
                            "{x-unit-discriminator, x-units} object")
                )
                continue
            if enclosing_props is None or nested_disc not in enclosing_props:
                failures.append(
                    Failure(source_file, f"{subpath}/x-unit-discriminator",
                            "x-unit-discriminator-sibling", nested_disc,
                            "an existing sibling property name")
                )
            else:
                enum = discriminator_enum(enclosing_props[nested_disc], source_path)
                keys = set(nested_units.keys())
                if enum is None:
                    failures.append(
                        Failure(source_file, f"{subpath}/x-unit-discriminator",
                                "x-units-keys-equal-enum",
                                f"discriminator '{nested_disc}'",
                                "a discriminator with a resolvable enum "
                                "(enum, boolean, or $ref to an enum)")
                    )
                elif keys != enum:
                    failures.append(
                        Failure(source_file, f"{subpath}/x-units",
                                "x-units-keys-equal-enum", sorted(keys),
                                sorted(enum))
                    )
            validate_x_units_map(nested_units, f"{subpath}/x-units",
                                 enclosing_props, source_path, source_file,
                                 failures, allowed_units)
        else:
            failures.append(
                Failure(source_file, subpath, "x-units-value-type",
                        type(val).__name__,
                        "a unit string or a nested discriminator object")
            )


def check_annotations(node, path, source_file, source_path, properties_stack,
                      failures, allowed_units, col_allowed=None, prop_name=None):
    """Walk the schema recursively enforcing rules 2, 3, 4, 5, 6."""
    if col_allowed is None:
        col_allowed = {}
    if isinstance(node, dict):
        # Rule 5a: no "descriptor" keys.
        if "descriptor" in node:
            failures.append(
                Failure(source_file, path + "/descriptor", "no-descriptor",
                        "key present", "no 'descriptor' key (fold into description)")
            )
        # Rule 5b: no "type": null.
        if "type" in node and node["type"] is None:
            failures.append(
                Failure(source_file, path + "/type", "no-type-null",
                        "null", "a concrete type or type list")
            )

        # The set of sibling property names, if this node has a "properties" map.
        sibling_props = None
        if isinstance(node.get("properties"), dict):
            sibling_props = node["properties"]

        # Rule 2: x-unit value.
        if "x-unit" in node:
            val = node["x-unit"]
            if val not in allowed_units:
                failures.append(
                    Failure(source_file, path + "/x-unit", "x-unit-vocabulary",
                            val, "a unit in Core/units.json allowed_units or 'pu'")
                )
            # Rule 2b: (quantity_type, unit) pairing. When the property name
            # matches a DB column registered in SiennaGridDB's
            # column_conventions.json, the unit must be one the registry allows
            # for that column's quantity_type — catches e.g. a reactive-power
            # field annotated 'MW' that the flat vocabulary check waves through.
            elif prop_name in col_allowed and val not in col_allowed[prop_name]:
                failures.append(
                    Failure(source_file, path + "/x-unit", "x-unit-quantity",
                            val,
                            f"a unit registered for column '{prop_name}': "
                            f"{sorted(col_allowed[prop_name])}")
                )

        # Rules 3 & 4 apply to annotations on a property. The property's
        # sibling set is the enclosing "properties" map (properties_stack top).
        enclosing_props = properties_stack[-1] if properties_stack else None

        # Rule 2: x-units values (recursive — supports nested discriminators for
        # multi-dimensional units).
        if "x-units" in node and isinstance(node["x-units"], dict):
            validate_x_units_map(node["x-units"], f"{path}/x-units",
                                 enclosing_props, source_path, source_file,
                                 failures, allowed_units)

        # Rule 3: x-unit-base names an existing sibling property.
        if "x-unit-base" in node:
            base = node["x-unit-base"]
            if enclosing_props is None or base not in enclosing_props:
                failures.append(
                    Failure(source_file, path + "/x-unit-base", "x-unit-base-sibling",
                            base, "an existing sibling property name")
                )

        # Rules 3 & 4: x-unit-discriminator.
        if "x-unit-discriminator" in node:
            disc = node["x-unit-discriminator"]
            if enclosing_props is None or disc not in enclosing_props:
                failures.append(
                    Failure(source_file, path + "/x-unit-discriminator",
                            "x-unit-discriminator-sibling", disc,
                            "an existing sibling property name")
                )
            elif "x-units" in node and isinstance(node["x-units"], dict):
                disc_schema = enclosing_props[disc]
                enum = discriminator_enum(disc_schema, source_path)
                keys = set(node["x-units"].keys())
                if enum is None:
                    failures.append(
                        Failure(source_file, path + "/x-unit-discriminator",
                                "x-units-keys-equal-enum", f"discriminator '{disc}'",
                                "a discriminator with a resolvable enum (enum, boolean, or $ref to an enum)")
                    )
                elif keys != enum:
                    failures.append(
                        Failure(source_file, path + "/x-units",
                                "x-units-keys-equal-enum", sorted(keys),
                                sorted(enum))
                    )

        # Rule 6: property literally named "unit"/"units" typed string.
        if sibling_props is not None:
            for pname, pschema in sibling_props.items():
                if pname in ("unit", "units") and isinstance(pschema, dict):
                    if pschema.get("type") == "string":
                        desc = pschema.get("description", "")
                        desc_lower = desc.lower()
                        if "units.json" not in desc_lower and "vocabulary" not in desc_lower:
                            failures.append(
                                Failure(source_file,
                                        f"{path}/properties/{pname}/description",
                                        "unit-property-description",
                                        repr(desc),
                                        "a description mentioning Core/units.json or the unit vocabulary")
                            )

        # Recurse. When entering "properties", push its map so nested property
        # schemas see it as the enclosing sibling set.
        for k, v in node.items():
            child_path = f"{path}/{k}"
            if k == "properties" and isinstance(v, dict):
                for pname, pschema in v.items():
                    check_annotations(pschema, f"{child_path}/{pname}", source_file,
                                      source_path, properties_stack + [v], failures,
                                      allowed_units, col_allowed, pname)
            else:
                check_annotations(v, child_path, source_file, source_path,
                                  properties_stack, failures, allowed_units,
                                  col_allowed, prop_name)

    elif isinstance(node, list):
        for i, item in enumerate(node):
            check_annotations(item, f"{path}/{i}", source_file, source_path,
                              properties_stack, failures, allowed_units,
                              col_allowed, prop_name)


def check_metaschema(doc, source_file, failures):
    """Rule 1: validate against the draft-07 meta-schema."""
    try:
        Draft7Validator.check_schema(doc)
    except jsonschema.exceptions.SchemaError as exc:
        loc = "/" + "/".join(str(p) for p in exc.absolute_path)
        failures.append(
            Failure(source_file, loc, "draft-07-meta-schema",
                    exc.message, "a valid draft-07 schema")
        )


# --------------------------------------------------------------------------- #
# Description channel (--fix-descriptions / --check-descriptions)
#
# Every property carrying x-unit / x-units must have a description ENDING with a
# canonical "Units: ..." sentence. Descriptions are the only annotation channel
# that reaches generated Julia/Python code (see docs/UNIT_ANNOTATIONS.md), so
# the units are carried there systematically.
#
# Sentence format:
#   * plain x-unit:   "Units: <x-unit>."
#   * discriminated:  "Units: per <discriminator> — <VAL>: <unit>, ... ."
#                     (x-units entries in insertion order; em dash separator).
#   * nested:         a discriminated value may itself be discriminated; it
#                     renders parenthesized: "<VAL>: (per <discriminator> — ...)".
# The sentence is applied idempotently: a stale trailing "Units: ..." sentence
# is replaced, never duplicated.
# --------------------------------------------------------------------------- #

# Strip ONLY a trailing canonical "Units: ..." sentence. The match runs from a
# "Units:" to the terminal period and may not cross a sentence boundary (". "):
# the tempered `(?!\. )` forces the engine past any earlier "Units:" that a
# description mentions in prose (those are always followed by ". "), so it
# latches onto the final "Units: ..." sentence only. Anchoring to a preceding
# ". " is deliberately NOT required: existing canonical descriptions run prose
# straight into the sentence (".. to be nothing Units: MVAr."), so demanding a
# boundary there would break idempotency of --fix-descriptions.
_UNITS_SENTENCE_RE = re.compile(r"Units:(?:(?!\. ).)*\.\s*$", re.DOTALL)


def _units_value(value):
    """Render one x-units map value: a plain unit string, or a nested
    discriminated map rendered parenthesized as '(per <disc> — K: v, ...)'."""
    if isinstance(value, dict):
        disc = value.get("x-unit-discriminator", "value")
        parts = ", ".join(f"{k}: {_units_value(v)}" for k, v in value["x-units"].items())
        return f"(per {disc} — {parts})"
    return value


def units_sentence(node):
    """Return the canonical 'Units: ...' sentence for an annotated node."""
    if "x-units" in node and isinstance(node["x-units"], dict):
        disc = node.get("x-unit-discriminator", "value")
        parts = ", ".join(f"{k}: {_units_value(v)}" for k, v in node["x-units"].items())
        return f"Units: per {disc} — {parts} ."
    return f"Units: {node['x-unit']}."


def strip_units_sentence(desc):
    """Remove a trailing 'Units: ...' sentence, returning the cleaned prefix."""
    return _UNITS_SENTENCE_RE.sub("", desc)


def desired_description(node):
    """Return the description this annotated node should carry."""
    sentence = units_sentence(node)
    desc = node.get("description")
    if not isinstance(desc, str) or desc == "":
        return sentence
    prefix = strip_units_sentence(desc).rstrip()
    if prefix == "":
        return sentence
    return f"{prefix} {sentence}"


def has_unit_annotation(node):
    return isinstance(node, dict) and ("x-unit" in node or "x-units" in node)


def fix_descriptions_in_node(node):
    """Rewrite descriptions in place. Returns count of nodes changed."""
    changed = 0
    if isinstance(node, dict):
        if has_unit_annotation(node):
            want = desired_description(node)
            if node.get("description") != want:
                node["description"] = want
                changed += 1
        for v in node.values():
            changed += fix_descriptions_in_node(v)
    elif isinstance(node, list):
        for v in node:
            changed += fix_descriptions_in_node(v)
    return changed


def check_descriptions_in_node(node, path, source_file, failures):
    if isinstance(node, dict):
        if has_unit_annotation(node):
            want = desired_description(node)
            if node.get("description") != want:
                failures.append(
                    Failure(source_file, path + "/description",
                            "units-description-sentence",
                            repr(node.get("description")), repr(want))
                )
        for k, v in node.items():
            check_descriptions_in_node(v, f"{path}/{k}", source_file, failures)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            check_descriptions_in_node(v, f"{path}/{i}", source_file, failures)


def detect_indent(text):
    """Detect existing indentation from first indented line; fall back to 2."""
    for line in text.splitlines():
        m = re.match(r'^(\s+)"', line)
        if m:
            return len(m.group(1))
    return 2


def run_fix_descriptions(files):
    total = 0
    files_changed = 0
    for path in files:
        text = path.read_text()
        indent = detect_indent(text)
        doc = json.loads(text)
        n = fix_descriptions_in_node(doc)
        if n:
            with open(path, "w") as fh:
                json.dump(doc, fh, indent=indent, ensure_ascii=False)
                fh.write("\n")
            files_changed += 1
            total += n
            print(f"fixed {n} description(s): {path.relative_to(REPO_ROOT)}")
    print(f"\n--fix-descriptions: {total} description(s) across {files_changed} file(s).")
    return 0


def run_check_descriptions(files):
    failures = []
    for path in files:
        rel = path.relative_to(REPO_ROOT)
        try:
            doc = load_json(path)
        except json.JSONDecodeError as exc:
            failures.append(Failure(str(rel), "/", "json-parse", str(exc), "valid JSON"))
            continue
        check_descriptions_in_node(doc, "", str(rel), failures)
    print(f"Checked description sentences over {len(files)} schema file(s).")
    if failures:
        print(f"\n{len(failures)} failure(s):\n")
        for f in failures:
            print(f)
            print()
        print("Run: python scripts/validate_units.py --fix-descriptions")
        return 1
    print("\nOK: every annotated property ends with its canonical Units: sentence.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--fix-descriptions", action="store_true",
                       help="Rewrite annotated properties' descriptions to end "
                            "with the canonical Units: sentence (idempotent).")
    group.add_argument("--check-descriptions", action="store_true",
                       help="Exit non-zero if any annotated property lacks its "
                            "canonical Units: sentence (CI check).")
    args = parser.parse_args()

    files = collect_schema_files()
    if args.fix_descriptions:
        return run_fix_descriptions(files)
    if args.check_descriptions:
        return run_check_descriptions(files)
    return run_validation(files)


def run_validation(files):
    allowed_units = load_allowed_units()
    col_allowed = load_column_allowed_units()
    failures = []

    for path in files:
        rel = path.relative_to(REPO_ROOT)
        try:
            doc = load_json(path)
        except json.JSONDecodeError as exc:
            failures.append(
                Failure(str(rel), "/", "json-parse", str(exc), "valid JSON")
            )
            continue
        check_metaschema(doc, str(rel), failures)
        check_annotations(doc, "", str(rel), path, [], failures, allowed_units,
                          col_allowed)

    print(f"Scanned {len(files)} schema file(s) under {', '.join(SCAN_DIRS)}.")
    print(f"Vocabulary: {len(allowed_units) - 1} allowed units + 'pu' from Core/units.json.")
    if col_allowed:
        print(f"Quantity pairing: {len(col_allowed)} DB columns from "
              "SiennaGridDB/schema/column_conventions.json.")
    else:
        print("Quantity pairing: SiennaGridDB checkout absent -- flat check only.")

    if failures:
        print(f"\n{len(failures)} failure(s):\n")
        for f in failures:
            print(f)
            print()
        return 1

    print("\nOK: all annotation rules pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
