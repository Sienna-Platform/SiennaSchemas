"""Regression tests for check_refs default-type rule.

Run: ../.venv/bin/python3 tests/test_check_refs.py
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_refs as cr


def errors_for(properties):
    with tempfile.NamedTemporaryFile("w", suffix=".json", dir=ROOT, delete=False) as f:
        json.dump({"type": "object", "properties": properties}, f)
    path = Path(f.name)
    try:
        errors = []
        cr.check_default_types(path, errors)
        return errors
    finally:
        path.unlink()


def test_string_default_on_number_fails():
    assert len(errors_for({"P_ref": {"type": "number", "default": "1.0"}})) == 1


def test_bool_default_on_integer_fails():
    assert len(errors_for({"flag": {"type": "integer", "default": True}})) == 1


def test_float_default_on_integer_fails():
    assert len(errors_for({"n": {"type": "integer", "default": 1.0}})) == 1


def test_matching_defaults_pass():
    assert not errors_for(
        {
            "a": {"type": "number", "default": 1},
            "b": {"type": "integer", "default": 1},
            "c": {"type": ["number", "null"], "default": None},
            "d": {"type": "string", "default": "x"},
            "e": {"$ref": "#/x", "default": "COMPONENT_BASE"},
        }
    )


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("OK")
