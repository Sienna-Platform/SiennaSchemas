"""Regression tests for check_compat closure and membership rules.

Run: ../.venv/bin/python3 tests/test_compat_gate.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_compat as cc

PROPS = {"a": {"type": "number"}}


def diff(b, h):
    out = cc.Findings()
    cc.diff_schema(b, h, out, "t")
    return out


def kinds(out):
    return {f[0] for f in out}


def test_removed_explicit_open_is_breaking():
    b = {"type": "object", "properties": PROPS, "additionalProperties": True}
    h = {"type": "object", "properties": PROPS}
    assert cc.BREAKING in kinds(diff(b, h))


def test_explicit_false_matching_bundle_is_silent():
    b = {"type": "object", "properties": PROPS}
    h = {"type": "object", "properties": PROPS, "additionalProperties": False}
    assert not diff(b, h)


def test_first_property_closes_object():
    b = {"type": ["object", "null"]}
    h = {"type": ["object", "null"], "properties": PROPS}
    assert cc.BREAKING in kinds(diff(b, h))


def test_property_added_to_closed_object_is_feature():
    b = {"type": "object", "properties": PROPS}
    h = {"type": "object", "properties": {**PROPS, "b": {"type": "number"}}}
    assert kinds(diff(b, h)) == {cc.FEATURE}


def test_schema_valued_additional_still_recurses():
    b = {"type": "object", "additionalProperties": {"type": "number"}}
    h = {"type": "object", "additionalProperties": {"type": "string"}}
    out = diff(b, h)
    assert len(out) == 1 and out[0][0] == cc.BREAKING


def test_membership_removed_is_breaking():
    out = cc.Findings()
    cc.diff_membership({("D", "components"): {"A", "B"}}, {("D", "components"): {"A", "C"}}, out)
    assert sorted((f[0], f[2]) for f in out) == [(cc.BREAKING, "B"), (cc.FEATURE, "C")]


def test_moved_component_file_is_breaking():
    with tempfile.TemporaryDirectory() as tmp:
        head = Path(tmp) / "head"
        shutil.copytree(ROOT, head, ignore=shutil.ignore_patterns(".git"))
        src = head / "Operations/StaticInjection/InterruptiblePowerLoad.json"
        dst = head / "Operations/InterruptiblePowerLoad.json"
        text = src.read_text().replace('"../../', '"../')
        dst.write_text(text)
        src.unlink()
        for sel in head.glob("openapi-*.json"):
            t = sel.read_text()
            sel.write_text(t.replace("Operations/StaticInjection/InterruptiblePowerLoad.json", "Operations/InterruptiblePowerLoad.json"))
        out = cc.diff_trees(ROOT, head)
        assert any(f[0] == cc.BREAKING and "no longer in" in f[3] for f in out), list(out)


def tagged(required=True):
    def branch(tag):
        b = {"type": "object", "properties": {"kind": {"const": tag}}}
        if required:
            b["required"] = ["kind"]
        return b
    return branch


def test_oneof_paired_widening_is_breaking():
    b = {"oneOf": [{"type": "number"}, {"type": "string"}]}
    h = {"oneOf": [{"type": "number"}, {"type": ["string", "number"]}]}
    assert kinds(diff(b, h)) == {cc.BREAKING}


def test_oneof_added_branch_unpinned_is_breaking():
    b = {"oneOf": [{"type": "number"}, {"type": "string"}]}
    h = {"oneOf": [{"type": "number"}, {"type": "string"}, {"minimum": 0}]}
    assert kinds(diff(b, h)) == {cc.BREAKING}


def test_anyof_same_changes_stay_feature():
    b = {"anyOf": [{"type": "number"}, {"type": "string"}]}
    widened = {"anyOf": [{"type": "number"}, {"type": ["string", "number"]}]}
    added = {"anyOf": [{"type": "number"}, {"type": "string"}, {"minimum": 0}]}
    assert kinds(diff(b, widened)) == {cc.FEATURE}
    assert kinds(diff(b, added)) == {cc.FEATURE}


def test_oneof_pinned_added_branch_is_feature():
    t = tagged()
    b = {"oneOf": [t("a"), t("b")]}
    h = {"oneOf": [t("a"), t("b"), t("c")]}
    assert kinds(diff(b, h)) == {cc.FEATURE}


def test_oneof_tag_not_required_added_branch_is_breaking():
    t = tagged(required=False)
    b = {"oneOf": [t("a"), t("b")]}
    h = {"oneOf": [t("a"), t("b"), t("c")]}
    assert kinds(diff(b, h)) == {cc.BREAKING}


def test_oneof_ref_branches_resolve_to_pinned():
    t = tagged()
    defs = {"A": t("a"), "B": t("b"), "C": t("c")}
    def ref(n):
        return {"$ref": f"#/components/schemas/{n}"}

    out = cc.Findings()
    out.base_defs = out.head_defs = defs
    cc.diff_schema({"oneOf": [ref("A"), ref("B")]}, {"oneOf": [ref("A"), ref("B"), ref("C")]}, out, "t")
    assert kinds(out) == {cc.FEATURE}


def copy_without(selector):
    tmp = Path(tempfile.mkdtemp())
    copy = tmp / "tree"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git"))
    (copy / selector).unlink()
    return tmp, copy


def test_base_missing_selector_reports_schemas_added():
    tmp, base = copy_without("openapi-dynamics.json")
    try:
        out = cc.diff_trees(base, ROOT)
    finally:
        shutil.rmtree(tmp)
    added = [f for f in out if f[1].startswith("dynamics/") and f[3] == "named schema added"]
    assert added and all(f[0] == cc.FEATURE for f in added)


def test_head_missing_selector_still_raises():
    tmp, head = copy_without("openapi-dynamics.json")
    try:
        try:
            cc.diff_trees(ROOT, head)
        except FileNotFoundError:
            return
        raise AssertionError("missing head selector did not raise")
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK")
