#!/usr/bin/env python3
"""Driver for contributing to SiennaSchemas: scaffold, gate, and regenerate.

Stdlib only, but it must run under an interpreter that has `jsonschema`
importable, because the unit gates need it. `doctor` tells you which one.

    python3 .claude/skills/contribute-schemas/pipeline.py doctor
    python3 .claude/skills/contribute-schemas/pipeline.py scaffold ...
    python3 .claude/skills/contribute-schemas/pipeline.py fix
    python3 .claude/skills/contribute-schemas/pipeline.py check
    python3 .claude/skills/contribute-schemas/pipeline.py downstream

Run from the SiennaSchemas checkout root. Sibling repos are located relative
to it and every leg that needs one SKIPs cleanly when it is absent.
"""

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
WORKSPACE = REPO.parent
GRIDDB = WORKSPACE / "SiennaGridDB"
JULIA_MODELS = WORKSPACE / "PowerOpenAPIModels"
PY_MODELS = WORKSPACE / "power-openapi-models"

# Domain -> the openapi-*.json selector a new schema file must be listed in.
SELECTORS = {
    "Core": "openapi-core.json",
    "Operations": "openapi-operations.json",
    "Investments": "openapi-investments.json",
    "Dynamics": "openapi-dynamics.json",
    "TimeSeries": "openapi-timeseries.json",
}

GREEN, RED, YELLOW, DIM, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def say(tag, msg, color=OFF):
    print(f"{color}{tag:<5}{OFF} {msg}", flush=True)


def run(cmd, cwd=None, env=None, capture=True):
    """Run cmd, return (returncode, combined output)."""
    merged = dict(os.environ)
    if env:
        merged.update(env)
    p = subprocess.run(
        cmd,
        cwd=cwd or REPO,
        env=merged,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    return p.returncode, (p.stdout or "")


# ---------------------------------------------------------------------------
# interpreter discovery
# ---------------------------------------------------------------------------


def has_jsonschema(python):
    rc, _ = run([python, "-c", "import jsonschema"])
    return rc == 0


def find_python():
    """First interpreter that can import jsonschema. The gates need it."""
    candidates = [
        str(REPO / ".venv" / "bin" / "python3"),
        sys.executable,
        shutil.which("python3") or "python3",
    ]
    for c in candidates:
        if c and pathlib.Path(c).exists() and has_jsonschema(c):
            return c
    return None


def find_codegen_python():
    """Interpreter whose datamodel-codegen is new enough for the Makefile.

    The Makefile passes --allow-remote-refs, which 0.55.0 (the pin in
    power-openapi-models/.venv and its uv.lock) does not accept.
    """
    for venv in (WORKSPACE / ".venv", PY_MODELS / ".venv"):
        exe = venv / "bin" / "datamodel-codegen"
        if not exe.exists():
            continue
        rc, out = run([str(exe), "--help"])
        if rc == 0 and "--allow-remote-refs" in out:
            return venv / "bin"
    return None


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def cmd_doctor(args):
    ok = True

    py = find_python()
    if py:
        say("OK", f"gate interpreter (has jsonschema): {py}", GREEN)
    else:
        say("FAIL", "no interpreter with jsonschema. Fix:", RED)
        print(f"      {REPO}/.venv/bin/pip install jsonschema")
        ok = False

    bindir = find_codegen_python()
    if bindir:
        _, ver = run([str(bindir / "datamodel-codegen"), "--version"])
        say("OK", f"python codegen: {bindir} ({ver.strip()})", GREEN)
    else:
        say("SKIP", "no datamodel-codegen supporting --allow-remote-refs; python leg unavailable", YELLOW)

    if shutil.which("docker"):
        rc, out = run(["docker", "images", "-q", "power-codegen:local"])
        if rc == 0 and out.strip():
            say("OK", "julia codegen image power-codegen:local present", GREEN)
        else:
            say("SKIP", "no power-codegen:local image; julia leg unavailable. Build it:", YELLOW)
            print(f"      cd {JULIA_MODELS} && docker build -t power-codegen:local .")
    else:
        say("SKIP", "no docker; julia leg unavailable", YELLOW)

    for name, path in (("SiennaGridDB", GRIDDB), ("PowerOpenAPIModels", JULIA_MODELS),
                       ("power-openapi-models", PY_MODELS)):
        if path.is_dir():
            say("OK", f"sibling checkout {name}", GREEN)
        else:
            say("SKIP", f"sibling checkout {name} absent at {path}", YELLOW)

    return 0 if ok else 1


# ---------------------------------------------------------------------------
# scaffold
# ---------------------------------------------------------------------------


def component_stub(name, description):
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": name,
        "description": description,
        "type": "object",
        "properties": {
            "id": {"description": "Unique integer identifier for this component.", "type": "integer"},
            "name": {
                "description": "Name of the component. Components of the same type must have "
                               "unique names, but components of different types can share a name.",
                "type": "string",
            },
            "available": {
                "description": "Indicator of whether the component is connected and online "
                               "(`true`) or disconnected, offline, or down (`false`).",
                "type": "boolean",
            },
        },
        "required": ["id", "name", "available"],
    }


def attribute_stub(name, description):
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": name,
        "description": description,
        "type": "object",
        "properties": {
            "id": {"description": "Unique integer identifier for this attribute.", "type": "integer"},
            "name": {"description": "Identifier for this attribute.", "type": "string"},
            "available": {"description": "Whether this attribute is active.", "type": "boolean",
                          "default": True},
        },
        "required": ["id", "name"],
    }


def association_stub(name, description):
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": name,
        "description": description,
        "type": "object",
        "properties": {
            "owner_id": {"description": "ID of the owning entity. RENAME ME.", "type": "integer"},
            "entity_id": {"description": "ID of the associated entity. RENAME ME.", "type": "integer"},
        },
        "required": ["owner_id", "entity_id"],
    }


STUBS = {"component": component_stub, "attribute": attribute_stub, "association": association_stub}
DEFAULT_GROUP = {"attribute": "SupplementalAttributes", "association": "Associations"}


def register_in_selector(selector_path, name, ref):
    """Insert a $ref entry into an openapi-*.json selector, alphabetically."""
    raw = selector_path.read_text()
    doc = json.loads(raw)
    schemas = doc["components"]["schemas"]
    if name in schemas:
        return False
    later = [k for k in schemas if k > name]
    block = f'      "{name}": {{\n        "$ref": "{ref}"\n      }},\n'
    if later:
        anchor = f'      "{later[0]}": {{\n'
        if anchor not in raw:
            raise SystemExit(f"cannot locate anchor {later[0]} in {selector_path.name}")
        selector_path.write_text(raw.replace(anchor, block + anchor, 1))
    else:
        # Append as the last entry: the previous last one needs its comma.
        last = list(schemas)[-1]
        marker = f'      "{last}": {{\n        "$ref": "{schemas[last]["$ref"]}"\n      }}\n'
        if marker not in raw:
            raise SystemExit(f"cannot locate trailing entry {last} in {selector_path.name}")
        replacement = marker.replace("      }\n", "      },\n") + block.rstrip(",\n") + "\n"
        selector_path.write_text(raw.replace(marker, replacement, 1))
    return True


def cmd_scaffold(args):
    group = args.group or DEFAULT_GROUP.get(args.kind)
    if not group:
        raise SystemExit("--group is required for a component (e.g. StaticInjection, Branch, Topology)")

    target = REPO / args.domain / group / f"{args.name}.json"
    if target.exists():
        raise SystemExit(f"{target.relative_to(REPO)} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)

    body = STUBS[args.kind](args.name, args.description)
    target.write_text(json.dumps(body, indent=2) + "\n")
    say("NEW", target.relative_to(REPO), GREEN)

    selector = REPO / SELECTORS[args.domain]
    ref = f"{args.domain}/{group}/{args.name}.json"
    if register_in_selector(selector, args.name, ref):
        say("EDIT", f"{selector.name}: registered {args.name}", GREEN)

    print()
    print("Next:")
    print(f"  1. Add the real properties to {target.relative_to(REPO)}.")
    print("     Annotate every numeric with x-unit (or x-units + x-unit-discriminator).")
    print("     Ambiguous units ('pu', '1', 'ohm', 'S', 'm') also need x-quantity")
    print("     unless a sibling branch settles them; `check` tells you which.")
    if args.kind == "association":
        print("  2. Rename owner_id/entity_id, and add a top-level bucket for this relation")
        print("     to Core/SystemDocument.json -- an association is not in `components`.")
    elif args.kind == "attribute":
        print("  2. Attributes are linked by Core/Associations/SupplementalAttributeAssociation.json;")
        print("     no new association type is needed.")
    print(f"  {'3' if args.kind != 'component' else '2'}. pipeline.py fix && pipeline.py check")
    print(f"  {'4' if args.kind != 'component' else '3'}. pipeline.py downstream")
    return 0


# ---------------------------------------------------------------------------
# fix / check
# ---------------------------------------------------------------------------


def cmd_fix(args):
    py = find_python()
    if not py:
        raise SystemExit("no interpreter with jsonschema; run `pipeline.py doctor`")
    for step in (["scripts/validate_units.py", "--fix-descriptions"], ["scripts/bundle_specs.py"]):
        rc, out = run([py] + step)
        print(out.rstrip())
        if rc != 0:
            say("FAIL", " ".join(step), RED)
            return rc
    say("OK", "descriptions canonicalized and dist/ rebundled", GREEN)
    return 0


GATES = [
    ("unit annotations", ["scripts/validate_units.py"]),
    ("description channel", ["scripts/validate_units.py", "--check-descriptions"]),
    ("bundle freshness", ["scripts/bundle_specs.py", "--check"]),
    ("$ref resolution", ["scripts/check_refs.py"]),
    ("package layering", ["scripts/check_layering.py"]),
    ("time series fixtures", ["scripts/validate_fixtures.py"]),
]


def cmd_check(args):
    py = find_python()
    if not py:
        raise SystemExit("no interpreter with jsonschema; run `pipeline.py doctor`")
    failed = []
    for label, step in GATES:
        rc, out = run([py] + step)
        if rc == 0:
            say("OK", label, GREEN)
        else:
            failed.append(label)
            say("FAIL", label, RED)
            print(f"{DIM}{out.rstrip()}{OFF}")
    print()
    if failed:
        say("FAIL", f"{len(failed)}/{len(GATES)} gates failed: {', '.join(failed)}", RED)
        return 1
    say("OK", f"all {len(GATES)} gates pass", GREEN)
    return 0


# ---------------------------------------------------------------------------
# downstream
# ---------------------------------------------------------------------------


def leg_db(args):
    if not GRIDDB.is_dir():
        say("SKIP", "SiennaGridDB absent", YELLOW)
        return 0
    py3 = shutil.which("python3") or "python3"
    rc, out = run([py3, "scripts/generate_unit_registry.py"], cwd=GRIDDB)
    if rc != 0:
        say("FAIL", "generate_unit_registry.py", RED)
        print(out.rstrip())
        return 1
    say("OK", "unit_registry.sql regenerated", GREEN)

    rc, out = run([py3, "scripts/generate_sql_schema.py"], cwd=GRIDDB)
    if rc != 0:
        say("FAIL", "generate_sql_schema.py", RED)
        print(out.rstrip())
        return 1
    say("OK", "generated_schema.sql regenerated (REFERENCE only, not the applied DDL)", GREEN)

    rc, out = run([py3, "scripts/generate_sql_schema.py", "--check", "--diff"], cwd=GRIDDB)
    missing = [l for l in out.splitlines() if "MISSING in hand-written" in l]
    if missing:
        say("TODO", f"{len(missing)} table(s) proposed but absent from schema/schema.sql:", YELLOW)
        for line in missing:
            print(f"      {line.strip()}")
        print(f"      Copy the CREATE TABLE from {GRIDDB.name}/schema/generated_schema.sql into")
        print("      schema/schema.sql, add the matching DROP TABLE, and bump PRAGMA user_version.")

    rc, out = run([py3, "scripts/check_units_sync.py"], cwd=GRIDDB)
    fails = [l for l in out.splitlines() if l.startswith("SUMMARY")]
    if rc != 0:
        say("FAIL", "check_units_sync.py", RED)
        print(out.rstrip())
        return 1
    say("OK", f"check_units_sync: {fails[0] if fails else 'clean'}", GREEN)
    return 0


def leg_py(args):
    if not PY_MODELS.is_dir():
        say("SKIP", "power-openapi-models absent", YELLOW)
        return 0
    bindir = find_codegen_python()
    if not bindir:
        say("SKIP", "no datamodel-codegen with --allow-remote-refs", YELLOW)
        return 0
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}
    rc, out = run(["make", "generate"], cwd=PY_MODELS, env=env)
    if rc != 0:
        say("FAIL", "power-openapi-models: make generate", RED)
        print(out.rstrip()[-3000:])
        return 1
    say("OK", "python models regenerated", GREEN)
    rc, out = run(["make", "validate"], cwd=PY_MODELS, env=env)
    if rc != 0:
        say("FAIL", "power-openapi-models: make validate", RED)
        print(out.rstrip()[-3000:])
        return 1
    say("OK", "python models validate", GREEN)
    _, st = run(["git", "status", "--short"], cwd=PY_MODELS)
    if st.strip():
        say("DIFF", f"{len(st.strip().splitlines())} file(s) changed in power-openapi-models", YELLOW)
    return 0


def leg_jl(args):
    if not JULIA_MODELS.is_dir():
        say("SKIP", "PowerOpenAPIModels absent", YELLOW)
        return 0
    if not shutil.which("docker"):
        say("SKIP", "no docker", YELLOW)
        return 0
    rc, out = run(["docker", "images", "-q", "power-codegen:local"])
    if not out.strip():
        say("SKIP", "power-codegen:local not built; see `pipeline.py doctor`", YELLOW)
        return 0
    rc, out = run([
        "docker", "run", "--rm",
        "-v", f"{REPO}:/schemas:ro",
        "-v", f"{JULIA_MODELS}:/output",
        "power-codegen:local",
    ])
    if rc != 0:
        say("FAIL", "julia codegen", RED)
        tail = [l for l in out.splitlines() if "ERROR" in l or "QUANTITY_OVERRIDES" in l]
        print("\n".join(tail) if tail else out.rstrip()[-3000:])
        if "QUANTITY_OVERRIDES" in out:
            print()
            print("      A property annotated \"pu\" is ambiguous across quantity types.")
            print(f"      Add the (Type, property) pair to QUANTITY_OVERRIDES in")
            print(f"      {JULIA_MODELS.name}/scripts/emit_units.jl and rerun.")
        return 1
    say("OK", "julia models regenerated", GREEN)
    _, st = run(["git", "status", "--short"], cwd=JULIA_MODELS)
    if st.strip():
        say("DIFF", f"{len(st.strip().splitlines())} file(s) changed in PowerOpenAPIModels", YELLOW)
    print(f"      Validate with: cd {JULIA_MODELS} && julia --project=<env-with-TimeZones> test/validate.jl")
    return 0


LEGS = {"db": leg_db, "py": leg_py, "jl": leg_jl}


def cmd_downstream(args):
    wanted = args.legs.split(",") if args.legs else list(LEGS)
    bad = [l for l in wanted if l not in LEGS]
    if bad:
        raise SystemExit(f"unknown leg(s): {', '.join(bad)} (choose from {', '.join(LEGS)})")
    status = 0
    for leg in wanted:
        print(f"\n--- {leg} ---")
        status |= LEGS[leg](args)
    return status


# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="report which interpreters/images each leg can use")
    sub.add_parser("fix", help="canonicalize Units: sentences and rebundle dist/")
    sub.add_parser("check", help="run the six in-repo gates")

    s = sub.add_parser("scaffold", help="create a new schema file and register it")
    s.add_argument("kind", choices=sorted(STUBS))
    s.add_argument("name", help="PascalCase schema title, e.g. SynchronousCondenser")
    s.add_argument("--domain", default="Operations", choices=sorted(SELECTORS))
    s.add_argument("--group", help="subdirectory, e.g. StaticInjection, Branch, Topology")
    s.add_argument("--description", default="TODO: describe this type.")

    d = sub.add_parser("downstream", help="regenerate the model packages and the DB artifacts")
    d.add_argument("--legs", help="comma-separated subset of: db,py,jl")

    args = ap.parse_args()
    return {
        "doctor": cmd_doctor,
        "fix": cmd_fix,
        "check": cmd_check,
        "scaffold": cmd_scaffold,
        "downstream": cmd_downstream,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
