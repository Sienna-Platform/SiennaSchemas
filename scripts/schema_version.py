#!/usr/bin/env python3
"""Reference implementation of the schema-version reader rule (docs/VERSIONING.md).

`classify` runs on the raw parsed document, before decode. Every binding must
agree with it on tests/fixtures/versioning/cases.json.
"""

import json
import re
import sys

PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$"
_RE = re.compile(PATTERN)


def parse(s):
    """Return (major, minor, patch, prerelease_or_None); ValueError if malformed."""
    m = _RE.fullmatch(s) if isinstance(s, str) else None
    if m is None:
        raise ValueError(f"malformed schema version: {s!r}")
    pre = m.group(4)
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), pre[1:] if pre else None


def line(v):
    """Compatibility line: MAJOR for >= 1.0, (0, MINOR) for 0.x."""
    major, minor = parse(v)[:2]
    if major == 0:
        return (0, minor)
    return major


def classify(reader, document):
    if "schema_version" not in document:
        return "missing"
    d = document["schema_version"]
    try:
        dv = parse(d)
    except ValueError:
        return "malformed"
    rv = parse(reader)
    if (dv[3] is not None or rv[3] is not None) and dv != rv:
        return "incompatible"
    if line(d) != line(reader):
        return "incompatible"
    if dv[:3] > rv[:3]:
        return "newer"
    if dv[:3] < rv[:3]:
        return "upgradable"
    return "current"


def _line_name(v):
    ln = line(v)
    if isinstance(ln, tuple):
        return f"{ln[0]}.{ln[1]}"
    return str(ln)


def message(outcome, reader, document_version):
    """The five canonical error texts; docs/VERSIONING.md lists them and every binding copies them."""
    d = document_version
    if outcome == "missing":
        return (
            "document has no schema_version: it predates versioning; re-export it with a "
            "current producer (psy5 bundles: PowerSystemsUpdater)"
        )
    if outcome == "malformed":
        encoded = json.dumps(d, separators=(",", ":"), ensure_ascii=False)
        return f"document schema_version {encoded} is not a valid version"
    if outcome == "incompatible":
        if parse(d)[3] is not None or parse(reader)[3] is not None:
            return (
                f"document written by schema {d} cannot be read by schema {reader}: "
                "dev builds read only their own output"
            )
        return (
            f"document written by schema {d} (line {_line_name(d)}) cannot be read by schema "
            f"{reader} (line {_line_name(reader)}): documents do not cross compatibility lines; "
            "migrating between lines is a separate upgrade tool's job "
            "(psy5 bundles: PowerSystemsUpdater)"
        )
    if outcome == "newer":
        return (
            f"document written by schema {d}; this reader understands up to {reader}; "
            f"update the model package to one built from >= {d}"
        )
    raise ValueError(f"outcome {outcome!r} is not an error")


if __name__ == "__main__":
    print(classify(sys.argv[1], json.load(open(sys.argv[2]))))
