#!/usr/bin/env python3
"""Generate a Markdown type reference from the bundled OpenAPI specs.

For each ``openapi-<domain>-bundled.json`` in ``dist/`` this writes one page per
component schema, one index page per package, and a root index page, under
``docs/reference/`` (or ``--out``).

It also writes ``docs/units.md`` (or ``--units-out``), the site's one narrative
page, whose two vocabulary tables are generated from ``Core/units.json`` so
they cannot drift from it.

Why this exists
----------------
The 183 hand-written JSON Schema types in this repo have no published, readable
form. The bundled specs (see ``bundle_specs.py``) already resolve every
``$ref`` and merge ``x-unit*`` annotations onto the property that carries them
-- exactly the content a reference page needs. The one thing bundling throws
away is *which file a type was authored in*, so this script also reads the
unbundled ``openapi-<domain>.json`` selectors, whose ``$ref`` values still
name the source file.

Shape coverage
--------------
A schema is rendered one of four ways:

* **Plain object** (has ``properties``) -- a property table. Property order is
  a Sienna convention (``id``, ``name``, ``bus``, ...) and is preserved
  exactly; only the *package type index* tables are sorted by name.
* **String enum** (has ``enum``) -- a ``## Values`` list.
* **Discriminated union** (``oneOf`` + ``discriminator``) -- a ``## Variants``
  table keyed by the discriminator's ``mapping``. A mapping target is linked
  only if it is a type in the same package; otherwise it is rendered as plain
  code text (cross-package links are out of scope for v1).
* Anything else (a handful of schemas: a string-keyed map, an ``anyOf`` of
  primitives, or a bare scalar type alias) gets a minimal best-effort section
  so the page is never empty, without over-fitting three-shape logic to
  one-off schemas.

Property **type** rendering: a property that resolved from a ``$ref`` during
bundling carries the referent's own ``title`` (e.g. ``MinMax``, ``PrimeMovers``)
verbatim from the shared definition, with no whitespace in it. A plain field's
auto-generated title (e.g. ``must_run`` -> ``"Must Run"``) always contains a
space and is deliberately ignored in favor of the JSON Schema ``type``
keyword -- that space is the only signal, from bundled content alone, that
distinguishes an authored field label from a shared type's own name.

Units: ``x-unit`` renders verbatim in its own column. ``x-units`` +
``x-unit-discriminator`` renders as ``alt1 / alt2 (by <discriminator>)``. The
column header links once per page to ``docs/UNIT_ANNOTATIONS.md`` on GitHub,
since the narrative docs are not site pages by decision.

Descriptions are the product: the schema's own description is passed through
verbatim (paragraph breaks kept) in the page body; a property's description in
the properties table has newlines collapsed to spaces and ``|`` escaped so it
cannot break the table -- and is never truncated.

Determinism
-----------
Identical input produces byte-identical output: property order is read
straight off the (order-preserving) parsed JSON, dict iteration order is
never hash-dependent, and only the type-index tables are explicitly sorted.

Usage
-----
    python scripts/generate_schema_docs.py [--out DIR] [--clean]
    python scripts/generate_schema_docs.py --nav          # print nav YAML, write nothing

Stdlib only.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"
GITHUB_BLOB = "https://github.com/Sienna-Platform/SiennaSchemas/blob/main"
UNIT_DOCS_URL = f"{GITHUB_BLOB}/docs/UNIT_ANNOTATIONS.md"

# SiennaGridDB has no dedicated units-architecture page on its default branch;
# its README's own "## Units" section is the live, cross-linkable target.
GRIDDB_UNITS_URL = "https://github.com/NREL-Sienna/SiennaGridDB/blob/main/README.md#units"

DOMAINS = [
    "infrastructure-core",
    "timeseries",
    "core",
    "operations",
    "investments",
    "dynamics",
]

# Nav label (the group display name from README.md's "What is defined" table --
# the site must agree with the README, so read it from there rather than
# inventing one) and generated-package name per domain, plus the domains
# (from this same list) each one depends on. Mirrors the "Generated package
# membership" table in .claude/CLAUDE.md.
PACKAGE_META = {
    "infrastructure-core": {
        "label": "Shared basics",
        "package": "InfrastructureCoreOpenAPIModels",
        "depends_on": [],
    },
    "timeseries": {
        "label": "Time series",
        "package": "InfrastructureTimeSeriesOpenAPIModels",
        "depends_on": ["infrastructure-core"],
    },
    "core": {
        "label": "Costs and curves",
        "package": "PowerCoreOpenAPIModels",
        "depends_on": ["infrastructure-core"],
    },
    "operations": {
        "label": "Grid topology and equipment",
        "package": "PowerOperationsOpenAPIModels",
        "depends_on": ["core"],
    },
    "investments": {
        "label": "Investment planning",
        "package": "PowerInvestmentsOpenAPIModels",
        "depends_on": ["core"],
    },
    "dynamics": {
        "label": "Machine dynamics",
        "package": "PowerDynamicsOpenAPIModels",
        "depends_on": ["core"],
    },
}


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def load_domain(domain):
    """Return (bundled_spec, source_paths) for one domain.

    source_paths maps each type name to the relative file path it was
    authored in, read from the unbundled selector (bundling discards this).
    """
    bundled = load_json(DIST_DIR / f"openapi-{domain}-bundled.json")
    unbundled = load_json(REPO_ROOT / f"openapi-{domain}.json")
    source_paths = {}
    for name, entry in unbundled["components"]["schemas"].items():
        filepart = entry["$ref"].split("#", 1)[0]
        source_paths[name] = filepart
    return bundled, source_paths


def resolve(node, definitions):
    """Resolve a bare ``{"$ref": "#/$defs/X"}`` (or "#/components/...")
    node against the bundle's hoisted definitions, merging sibling keys on
    top (siblings win), mirroring bundle_specs.py's own merge rule. Returns
    the node unchanged if it carries no ``$ref`` or the target is not among
    the hoisted definitions (e.g. a same-file reference bundling left
    untouched)."""
    if not (isinstance(node, dict) and "$ref" in node):
        return node
    name = node["$ref"].rsplit("/", 1)[-1]
    target = definitions.get(name)
    if target is None:
        return node
    merged = dict(target)
    for key, value in node.items():
        if key != "$ref":
            merged[key] = value
    return merged


SCALAR_TYPES = {"number", "integer", "string", "boolean"}


def render_type(node, definitions, known_schemas):
    """Render a property's type: integer/string/number/boolean, 'array of
    <T>', 'object', a shared definition's title (from a resolved $ref), or a
    ' or '-joined union.

    A property's own ``title`` is ambiguous after bundling: it is either a
    decorative, auto-generated label sitting beside a concrete scalar type
    (``{"title": "Max", "type": "number"}``) or the name of the shared type a
    ``$ref`` resolved to (``{"title": "MinMax", "type": "object", ...}``). A
    title with no whitespace is not enough to tell them apart on its own --
    ``FromTo_ToFrom.from_to`` carries the plain-looking title ``"FromTo"``
    while still being a scalar ``number``, and ``FromTo`` is a real (object)
    type name in this same bundle -- so "does the title resolve to a known
    schema" is not enough either. So: a concrete scalar type always wins over
    its title as the rendered type; the title is surfaced as a `(Title)`
    pointer alongside it only when the title names a known **enum** schema
    (an inlined `$ref` to a shared enum, e.g. `string (ACBusType)`), since
    that is the one case where the bare primitive genuinely loses meaning.
    A non-scalar type (`object`, `array`, or absent) prefers its title
    whenever that title names any known schema (enum or not), since a bare
    `object` tells the reader nothing. Everything else falls back to the old
    plain-title heuristic."""
    node = resolve(node, definitions)
    type_kw = node.get("type")
    if type_kw == "array":
        return f"array of {render_type(node.get('items', {}), definitions, known_schemas)}"
    title = node.get("title")
    has_plain_title = bool(title) and not re.search(r"\s", title)
    known_schema = known_schemas.get(title) if has_plain_title else None
    if isinstance(type_kw, str) and type_kw in SCALAR_TYPES:
        if known_schema is not None and "enum" in known_schema:
            return f"{type_kw} ({title})"
        return type_kw
    if known_schema is not None:
        return title
    if has_plain_title:
        return title
    if isinstance(type_kw, list):
        return " or ".join(type_kw)
    if "oneOf" in node:
        alts = dict.fromkeys(render_type(alt, definitions, known_schemas) for alt in node["oneOf"])
        return " or ".join(alts)
    if "anyOf" in node:
        alts = dict.fromkeys(render_type(alt, definitions, known_schemas) for alt in node["anyOf"])
        return " or ".join(alts)
    if type_kw:
        return type_kw
    return "object"


def render_unit_alt(value):
    """Render one x-units alternative: a plain unit string, or (rarely) a
    nested discriminated-unit object for an alternative that is itself
    conditional on a second property."""
    if isinstance(value, str):
        return value
    nested = " / ".join(render_unit_alt(v) for v in value["x-units"].values())
    nested_discriminator = value.get("x-unit-discriminator", "")
    return f"({nested} by {nested_discriminator})"


def render_unit(node):
    """Render the Unit column: verbatim x-unit, or the discriminated
    alternatives named by x-unit-discriminator, or '-'."""
    if "x-unit" in node:
        return f"`{node['x-unit']}`"
    if "x-units" in node:
        alts = " / ".join(render_unit_alt(v) for v in node["x-units"].values())
        discriminator = node.get("x-unit-discriminator", "")
        return f"`{alts} (by {discriminator})`"
    return "—"


def table_cell(text):
    """Collapse newlines to spaces and escape '|' so a description cannot
    break a Markdown table. Never truncates."""
    if not text:
        return "—"
    flat = re.sub(r"\s+", " ", text).strip()
    return flat.replace("|", "\\|")


def render_properties_section(schema, definitions, known_schemas):
    required = set(schema.get("required", []))
    lines = [
        "## Properties",
        "",
        f"| Property | Type | Required | [Unit]({UNIT_DOCS_URL}) | Description |",
        "|---|---|---|---|---|",
    ]
    for pname, pnode in schema["properties"].items():
        type_text = render_type(pnode, definitions, known_schemas)
        unit_text = render_unit(pnode)
        desc = table_cell(pnode.get("description", ""))
        req = "yes" if pname in required else "—"
        lines.append(f"| `{pname}` | `{type_text}` | {req} | {unit_text} | {desc} |")
    lines.append("")
    return lines


def render_values_section(schema):
    values = " · ".join(f"`{v}`" for v in schema["enum"])
    return ["## Values", "", values, ""]


def render_variants_section(schema, schema_names):
    discriminator = schema["discriminator"]
    prop = discriminator["propertyName"]
    mapping = discriminator.get("mapping", {})
    lines = [
        "## Variants",
        "",
        f"Discriminated by `{prop}`.",
        "",
        f"| `{prop}` | Schema |",
        "|---|---|",
    ]
    for key, target in mapping.items():
        tname = target.rsplit("/", 1)[-1]
        cell = f"[`{tname}`]({tname}.md)" if tname in schema_names else f"`{tname}`"
        lines.append(f"| `{key}` | {cell} |")
    lines.append("")
    return lines


def render_fallback_section(schema, definitions, known_schemas):
    """Best-effort rendering for the handful of schemas that are none of
    plain-object / string-enum / discriminated-union: a string-keyed map
    (additionalProperties), an anyOf of primitives, or a bare scalar type
    alias (rendered with no extra section beyond its description)."""
    if "additionalProperties" in schema:
        value_type = render_type(schema["additionalProperties"], definitions, known_schemas)
        lines = ["## Structure", "", f"A map from string keys to `{value_type}` values."]
        reserved = schema.get("propertyNames", {}).get("not", {}).get("enum")
        if reserved:
            lines.append("")
            lines.append("Reserved keys (each collides with a field name):")
            lines.append("")
            lines.append(" · ".join(f"`{r}`" for r in reserved))
        lines.append("")
        return lines
    if "anyOf" in schema:
        alts = dict.fromkeys(
            render_type(alt, definitions, known_schemas) for alt in schema["anyOf"]
        )
        return ["## Type", "", "One of: " + ", ".join(f"`{a}`" for a in alts) + ".", ""]
    return []


def build_known_schemas(bundled, definitions):
    """Map every schema name reachable from this domain's bundle (its own
    components.schemas, plus anything hoisted into its definitions) to its
    resolved schema body, so render_type can check what a title actually
    names -- in particular, whether it is an enum."""
    known = dict(definitions)
    for name, entry in bundled["components"]["schemas"].items():
        known.setdefault(name, resolve(entry, definitions))
    return known


def yaml_scalar(value):
    """Quote a YAML scalar only when it needs it -- a colon would otherwise
    split a plain `key: value` line."""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if ":" in text:
        return '"' + text.replace('"', '\\"') + '"'
    return text


def front_matter(fields):
    """Render a Jekyll/Just-the-Docs front matter block: '---'-delimited
    YAML, `fields` an ordered list of (key, value) pairs, as the very first
    lines of the page."""
    lines = ["---"]
    for key, value in fields:
        lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    lines.append("")
    return lines


def type_page(name, domain, schema, definitions, known_schemas, schema_names, source_path):
    lines = front_matter(
        [
            ("title", name),
            ("parent", PACKAGE_META[domain]["label"]),
            ("layout", "default"),
        ]
    )
    lines += [f"# {name}", ""]
    package = PACKAGE_META[domain]["package"]
    gh_url = f"{GITHUB_BLOB}/{source_path}"
    lines.append(f"**Package:** `{package}` · **Source:** [`{source_path}`]({gh_url})")
    lines.append("")
    description = schema.get("description")
    if description:
        lines.append(description.strip())
        lines.append("")
    if "enum" in schema:
        lines.extend(render_values_section(schema))
    elif "oneOf" in schema and "discriminator" in schema:
        lines.extend(render_variants_section(schema, schema_names))
    elif "properties" in schema:
        lines.extend(render_properties_section(schema, definitions, known_schemas))
    else:
        lines.extend(render_fallback_section(schema, definitions, known_schemas))
    return "\n".join(lines).rstrip("\n") + "\n"


def first_sentence(description):
    if not description:
        return "—"
    flat = re.sub(r"\s+", " ", description).strip()
    match = re.search(r"(.*?[.!?])(\s|$)", flat)
    sentence = match.group(1) if match else flat
    return table_cell(sentence)


def package_index_page(domain, bundled, definitions, type_names, info_description):
    meta = PACKAGE_META[domain]
    deps = meta["depends_on"]
    dep_text = ", ".join(f"`{PACKAGE_META[d]['package']}`" for d in deps) if deps else "None"
    nav_order = DOMAINS.index(domain) + 1
    lines = front_matter(
        [
            ("title", meta["label"]),
            ("has_children", True),
            ("layout", "default"),
            ("nav_order", nav_order),
        ]
    )
    lines += [
        f"# {meta['label']}",
        "",
        f"**Package:** `{meta['package']}` · **Depends on:** {dep_text}",
        "",
        info_description.strip(),
        "",
        "## Types",
        "",
        "| Type | Description |",
        "|---|---|",
    ]
    for name in sorted(type_names):
        schema = resolve(bundled["components"]["schemas"][name], definitions)
        lines.append(f"| [`{name}`]({name}.md) | {first_sentence(schema.get('description'))} |")
    lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def root_index_page(domain_info):
    lines = front_matter(
        [
            ("title", "Reference"),
            ("has_children", True),
            ("nav_order", 3),
            ("layout", "default"),
        ]
    )
    lines += [
        "# Schema Reference",
        "",
        "Reference docs for the hand-written JSON Schema types in this repo, "
        "generated from the bundled OpenAPI specs.",
        "",
        "Six packages, in dependency order:",
        "",
    ]
    for i, domain in enumerate(DOMAINS, start=1):
        meta = PACKAGE_META[domain]
        role = domain_info[domain].strip()
        lines.append(f"{i}. [{meta['label']}]({domain}/index.md) — {role}")
    lines.append("")
    lines.append(
        "Layering rule: the shared basics group carries no power semantics — "
        "every other package builds on it, directly or indirectly."
    )
    lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# The units page
# ---------------------------------------------------------------------------
#
# The site's one narrative page. It is generated rather than hand-written so
# that its two vocabulary tables come straight from Core/units.json and cannot
# drift from it; the prose around them lives in this module as the constants
# below. Same pipeline, same determinism guarantee as the reference tree.

UNITS_JSON = REPO_ROOT / "Core" / "units.json"
UNITS_JSON_URL = f"{GITHUB_BLOB}/Core/units.json"

# The type whose Unit column is walked through in "Reading a unit on a type
# page", and the three properties that show a plain unit, a discriminator, and
# a discriminated unit. Read from the bundle rather than transcribed, so the
# worked example cannot describe a version of Line that no longer exists;
# indexing is direct on purpose, so a removed property fails the build loudly.
UNITS_EXAMPLE_DOMAIN = "operations"
UNITS_EXAMPLE_TYPE = "Line"
UNITS_EXAMPLE_PROPERTIES = ["rating", "power_units", "parameter_units", "r"]

UNITS_LEAD = """\
Every numeric value in this data declares its unit. A field is megawatts, or ohms, or
seconds, because its definition says so — never because of how it is named, and never by
convention agreed somewhere else. This page is the vocabulary those declarations draw on
and the conventions they follow.
"""

UNITS_ROUTING = f"""\
## Where do I look?

| I have... | Look at |
|---|---|
| A schema field, and want its unit | That field's own `.json` file (`x-unit`, or `x-unit-discriminator` + `x-units`), read via [the decision procedure](#reading-a-value-the-decision-procedure) below |
| A question about the vocabulary itself — what quantities or units exist at all | [`Core/units.json`]({UNITS_JSON_URL}), read via [Reading `Core/units.json`](#reading-core-unitsjson) below |
| A new unit or annotation to add | [`docs/UNIT_ANNOTATIONS.md`]({UNIT_DOCS_URL}) — the authoring spec, a different job from reading |
| A SiennaGridDB database column or row value | [SiennaGridDB's units documentation]({GRIDDB_UNITS_URL}) — the registry mirrors this vocabulary; this page covers the JSON Schema side only |
"""

UNITS_MODEL = """\
## The self-contained blob principle

Every component JSON blob is completely interpretable on its own. There is no document-level
unit context: `Core/SystemDocument.json` carries no `unit_system` and no `base_power` for the
document as a whole. Anyone holding a single component blob — one `ThermalStandard`, one
`Line` — can read every value on it without the enclosing document, because everything a
value needs is recorded on that same blob: a discriminator property that says which basis
applies, and, for a per-unit reading, a `base_power` (and voltage base, where applicable) to
per-unitize against.

One consequence: a document can be — and often legitimately is — **mixed-basis**. One
`ThermalStandard` can carry its power fields in `NATURAL_UNITS` while the `Line` next to it
carries its in `COMPONENT_BASE`, each on its own recorded base. Nothing about the document
constrains this; each blob is read independently.

## Reading a value: the decision procedure

Given a numeric field on a component blob, read it in this order:

1. **Plain `x-unit`?** The field is always in that fixed unit, full stop — no discriminator,
   no basis, no document context. `base_power` is always `MVA`. `angle_limits` is always
   `rad`. `time_limits` is always `min`.
2. **`x-unit-discriminator` + `x-units`?** The field's unit depends on a sibling property
   recorded on the *same blob*. Read that sibling first, then look up the unit for its value
   in the `x-units` map. The discriminator is one of:
   - **`power_units`** — governs every power-family field on the component: active/reactive/
     apparent power, ratings, limits, ramp rates. Required on every component that has such a
     field, with **no default** — a producer must always declare it explicitly.
   - the established per-family discriminators: `parameter_units` (branch impedance —
     resistance, reactance, susceptance, conductance), `admittance_units` (shunt admittance),
     `dc_voltage_units` / `setpoint_voltage_units` / `voltage_units` (voltage setpoints),
     `energy_units` (stored energy), and others named on the type's own reference page.
3. **Discriminator value is `COMPONENT_BASE`?** Per-unitize against the basis this same blob
   records for itself — its own `base_power` (in MVA), and its own voltage base where the
   field is voltage-related. Never a document-wide table, never another component's base.

## `UnitSystem`'s three scopes

`UnitSystem` is a two-member enum — `COMPONENT_BASE`, `NATURAL_UNITS` — and it is used in
exactly three scopes, each read against its own record rather than anything document-wide:

- **A component's own `power_units`.** Governs that component's power-family fields, read
  against that component's own `base_power`. Required, no default.
- **A cost payload's own `power_units`.** The x-axis basis of a `CostCurve` or `FuelCurve` —
  the value curves wrapped by a generation cost's `variable_operation_cost` member (renamed
  from `variable` in this design pass). Read against the *owning component's* `base_power`,
  same as any other `COMPONENT_BASE` reading. Note the field-level asymmetry here:
  `CostCurve.power_units` does carry a default (`NATURAL_UNITS`) even though it stays in the
  schema's `required` list, while `FuelCurve.power_units` — like every component-level
  `power_units` — has none.
- **A time series association's own `unit_system`.** Governs only that one series; it says
  nothing about the component it is attached to, or any other series.

There is deliberately **no system-wide basis**. Data that was historically per-unitized
against a system base records that base in the component's own `base_power` and rides as
`COMPONENT_BASE` — the basis moved from "the system" to "this component's copy of what the
system's base happened to be." Mixed-basis documents are the expected result, not an edge
case: different components, in different bases, each self-describing.

**Read the discriminator before you read the number.** A per-unit value taken for a physical
one — or the reverse — is wrong by the size of the base, typically a factor of a hundred, and
the number itself will not warn you: `0.85` is an entirely plausible per-unit reactance and an
entirely plausible count of ohms.
"""

UNITS_QUANTITY_TYPES_LEAD = """\
## Quantity types

A quantity type is what is being measured: active power, voltage, a heat rate. Each one
fixes a default unit and a dimension — an exponent map over the base dimensions M (mass,
kg), L (length, m), T (time, s) and I (electric current, A), plus two pseudo-dimensions,
USD for money and Btu for the chemical energy in fuel. Dimensionless quantities carry the
empty map.
"""

UNITS_ALLOWED_LEAD = """\
## Allowed units and conversions

These are the units a value of each quantity type may be stored in. Nothing else is
accepted. **Factor to default** is the exact number a value is multiplied by to convert it
into that quantity's default unit — `0.001` takes kW to MW.

One kind of entry is not a conversion at all:

- **`—`** — conversion is context-dependent rather than a fixed factor. Every such row is a
  `pu` unit: what a per-unit value comes out as depends on the base the component recorded,
  which no constant can capture.
"""

# Emitted only while some row actually carries it, so the legend never
# describes an entry the table below does not contain.
UNITS_ALLOWED_ZERO_NOTE = """\
- **`0.0`** — this layer deliberately does not convert, and leaves the conversion to a layer
  that has the context for it.
"""

UNITS_ALLOWED_FREQUENCY_NOTE = """\
And one entry looks like a conversion but is not. **`Frequency` / `Hz` carries `60.0`, which
records the nominal system frequency, not a factor.** Hz is already Frequency's default
unit, so a genuine conversion factor there would be `1.0`. Do not multiply by it.
"""

UNITS_CONVENTIONS = """\
## Conventions worth knowing

### Reactive power is MVAr, never MW

Active, reactive and apparent power share one dimension, so nothing in the arithmetic
separates them. The vocabulary does: three quantity types with three units — `MW`, `MVAr`,
`MVA` — and a field's declared unit says which of the three it holds.

### Percent is banned; store fractions

There is no `%` unit and there will not be one. An efficiency of 95% is stored as `0.95`,
with the unit `1`. Every fraction, loss factor, and cofire level in this data reads that way.

### Time is three quantity types, not one

Time is split across three quantity types, each with exactly one allowed unit, so the
vocabulary itself enforces which tier a field belongs to:

| Quantity type | Unit | For |
|---|---|---|
| `Duration` | `s` | continuous-time constants — machine and controller dynamics |
| `OperationalDuration` | `min` | scheduling and commitment — minimum up and down times, ramp and reserve windows |
| `CalendarPeriod` | `yr` | long-term planning spans |

**Hours are not a unit in this vocabulary.** A field holding hours is a tier error, not a
conversion — it belongs in one of these three tiers, expressed in that tier's unit. Only
`min → s` is bridged, and it is bridged by an exact physical factor. Years are deliberately
not bridged at all: a calendar year is not a fixed number of seconds, so any factor would
quietly commit every consumer to one 365-day convention.

The tier fixes the unit, not the numeric type. `CalendarPeriod` is a whole number of years
throughout. `OperationalDuration` is real-valued, because a thirty-second reserve product is
a legitimate half-minute.

### Fuel energy is kept apart from electrical energy

`Btu` is its own pseudo-dimension rather than a form of electrical energy. Heat rates
(`MMBtu/MWh`) and start-up fuel (`MMBtu/MW`) therefore never share a dimension with a cost
quantity, and a mix-up between fuel intensity and cost is a dimension error rather than a
plausible-looking number.

### Angle converts, so degrees are accepted

`rad` is the default, and `deg` is registered with the exact factor π/180. Source data in
degrees converts on the way in rather than being rejected at the door.

### Dimensionless quantities use the unit `1`

The empty exponent map, and a single unit spelled `1`. `Dimensionless`, `Fraction` and
`PowerFactor` all share it: what separates them is what the number means, not how it is
scaled.
"""

# The blob narrated in "Worked example" below. Read from the bundle rather
# than transcribed (see validate_worked_example), so the walkthrough cannot
# describe a version of ThermalStandard that no longer exists; the illustrative
# JSON values themselves (100.0, "GEN-1", ...) are narrative sample data, not
# derived from any schema.
UNITS_WORKED_EXAMPLE_DOMAIN = "operations"
UNITS_WORKED_EXAMPLE_TYPE = "ThermalStandard"

UNITS_WORKED_EXAMPLE = """\
## Worked example: reading a `ThermalStandard` blob

Take one component, `ThermalStandard`, and its power-family fields: `active_power`,
`rating`, `active_power_limits`, `ramp_limits`, plus the fixed-unit `base_power` and
`time_limits`. Below is the same generator recorded twice — once with `power_units:
NATURAL_UNITS`, once with `power_units: COMPONENT_BASE` — at the same physical operating
point, on a 100 MVA base. (Abbreviated to the fields this example needs — a real
`ThermalStandard` blob also carries `status`, `bus`, `operation_cost`, and the rest of its
required fields.)

**`NATURAL_UNITS`:**

```json
{
  "id": 1,
  "name": "GEN-1",
  "power_units": "NATURAL_UNITS",
  "base_power": 100.0,
  "active_power": 100.0,
  "rating": 150.0,
  "active_power_limits": { "min": 20.0, "max": 150.0 },
  "ramp_limits": { "up": 50.0, "down": 50.0 },
  "time_limits": { "up": 480.0, "down": 480.0 }
}
```

**`COMPONENT_BASE`**, same generator, same operating point:

```json
{
  "id": 1,
  "name": "GEN-1",
  "power_units": "COMPONENT_BASE",
  "base_power": 100.0,
  "active_power": 1.0,
  "rating": 1.5,
  "active_power_limits": { "min": 0.2, "max": 1.5 },
  "ramp_limits": { "up": 0.5, "down": 0.5 },
  "time_limits": { "up": 480.0, "down": 480.0 }
}
```

Walking the decision procedure on three of these fields:

- **`base_power`** carries a plain `x-unit: MVA`. It is always megavolt-amperes, in both
  blobs, regardless of `power_units` — it is the anchor the discriminated fields are read
  against, not itself subject to the discriminator.
- **`rating`** carries `x-unit-discriminator: power_units` with `x-units: {NATURAL_UNITS:
  MVA, COMPONENT_BASE: pu}`. Read `power_units` first. In the first blob it is
  `NATURAL_UNITS`, so `rating: 150.0` means 150 MVA directly. In the second it is
  `COMPONENT_BASE`, so `rating: 1.5` is per-unit on this blob's own `base_power` — multiply
  by `base_power` (100 MVA) to recover the same 150 MVA. Nothing outside this one blob is
  needed to make that conversion.
- **`time_limits`** carries a plain `x-unit: min`. It reads `{up: 480.0, down: 480.0}` minutes
  in *both* blobs — `power_units` governs power-family fields only, so a fixed-unit field is
  never affected by it, one of the reasons step 1 of the decision procedure comes before
  step 2.
"""


def validate_worked_example():
    """Raise loudly if the schema facts UNITS_WORKED_EXAMPLE and UNITS_MODEL
    narrate in prose (rather than render from a table) have drifted: which
    fields exist on ThermalStandard and what discriminates them, and the
    CostCurve-vs-FuelCurve `power_units` default asymmetry."""
    bundled, _ = load_domain(UNITS_WORKED_EXAMPLE_DOMAIN)
    definitions = bundled.get("$defs", {})
    schema = resolve(bundled["components"]["schemas"][UNITS_WORKED_EXAMPLE_TYPE], definitions)
    props = schema["properties"]
    required = set(schema.get("required", []))
    checks = [
        (
            resolve(props["base_power"], definitions).get("x-unit") == "MVA",
            f"{UNITS_WORKED_EXAMPLE_TYPE}.base_power is no longer x-unit MVA",
        ),
        (
            resolve(props["time_limits"], definitions).get("x-unit") == "min",
            f"{UNITS_WORKED_EXAMPLE_TYPE}.time_limits is no longer x-unit min",
        ),
        (
            resolve(props["rating"], definitions).get("x-unit-discriminator") == "power_units",
            f"{UNITS_WORKED_EXAMPLE_TYPE}.rating no longer discriminates on power_units",
        ),
        (
            resolve(props["rating"], definitions).get("x-units")
            == {"NATURAL_UNITS": "MVA", "COMPONENT_BASE": "pu"},
            f"{UNITS_WORKED_EXAMPLE_TYPE}.rating's x-units map changed",
        ),
        (
            "power_units" in required and "default" not in props["power_units"],
            f"{UNITS_WORKED_EXAMPLE_TYPE}.power_units is no longer required-with-no-default",
        ),
        (
            "base_power" in required,
            f"{UNITS_WORKED_EXAMPLE_TYPE}.base_power is no longer required",
        ),
    ]
    core_bundled, _ = load_domain("core")
    core_defs = core_bundled.get("$defs", {})
    cost_curve_power_units = core_defs.get("CostCurve", {}).get("properties", {}).get("power_units", {})
    fuel_curve_power_units = core_defs.get("FuelCurve", {}).get("properties", {}).get("power_units", {})
    checks += [
        (
            cost_curve_power_units.get("default") == "NATURAL_UNITS",
            "CostCurve.power_units no longer defaults to NATURAL_UNITS",
        ),
        (
            "default" not in fuel_curve_power_units,
            "FuelCurve.power_units unexpectedly gained a default",
        ),
    ]
    for ok, message in checks:
        if not ok:
            raise AssertionError(f"docs/units.md worked example is stale: {message}")


UNITS_READING_LEAD = """\
## Reading a unit on a type page

Every type's reference page carries a Unit column, and it shows one of two things.

**A plain unit** — the field is always in that unit. `ThermalStandard`'s `base_power` is
`MVA` in every blob, whatever else the blob says.

**A discriminated unit**, written `alt1 / alt2 (by <property>)` — the unit depends on the
value of another property on the same component. Branch impedance is the standard case: a
[`{type_name}`]({type_page}) stores `r` and `x` in either physical ohms or per-unit, and its
`parameter_units` property says which.
"""

UNITS_READING_TAIL = """\
So on a `Line` whose `parameter_units` reads `COMPONENT_BASE`, `r` is per-unit on that
line's own `base_power`; on a `Line` that reads `NATURAL_UNITS`, the same field is ohms. The
same line's `rating` is governed separately, by its own `power_units` — the two
discriminators are independent properties on the same blob, and a `Line` can (and often
does) carry one basis for its impedance and a different one for its power fields. Read the
relevant discriminator first, then the number.
"""

UNITS_AUTHORS = f"""\
## For schema authors

Adding or changing a unit annotation is a different job from reading one, and it has its own
spec: [`docs/UNIT_ANNOTATIONS.md`]({UNIT_DOCS_URL}) covers the `x-unit`, `x-unit-base`, and
`x-units` + `x-unit-discriminator` keywords, where an annotation may be placed, and what the
validator enforces. The vocabulary itself is [`Core/units.json`]({UNITS_JSON_URL}), which is
where both tables on this page come from.
"""


def render_factor(value):
    """Render a to_default factor exactly (repr round-trips a float), or '—'
    for the null that marks a context-dependent conversion."""
    if value is None:
        return "—"
    return f"`{value!r}`"


def render_quantity_types_table(units):
    lines = [
        "| Quantity type | Default unit | Description |",
        "|---|---|---|",
    ]
    for entry in units["quantity_types"]:
        lines.append(
            f"| `{entry['name']}` | `{entry['default_unit']}` "
            f"| {table_cell(entry['description'])} |"
        )
    lines.append("")
    return lines


def render_allowed_units_table(units):
    """One row per allowed unit, grouped by quantity type in vocabulary order
    (the allowed_units list itself is not grouped)."""
    by_quantity = {entry["name"]: [] for entry in units["quantity_types"]}
    for entry in units["allowed_units"]:
        by_quantity[entry["quantity_type"]].append(entry)
    lines = [
        "| Quantity type | Unit | Factor to default |",
        "|---|---|---|",
    ]
    for name, entries in by_quantity.items():
        for entry in entries:
            lines.append(f"| `{name}` | `{entry['unit']}` | {render_factor(entry['to_default'])} |")
    lines.append("")
    return lines


UNITS_READING_JSON = f"""\
## Reading `Core/units.json`

The vocabulary file has two lists, answering two different questions.

- **`quantity_types`** — one entry per *kind* of quantity: `name`, its `dimension` (an
  exponent map over the base dimensions), `default_unit`, `ucum` (the UCUM code for that
  default unit, or `null` where none applies), and a `description`. This is what is being
  measured, independent of how any one value happens to be stored — the source of the
  Quantity types table above.
- **`allowed_units`** — one row per `(quantity_type, unit)` pair that quantity may actually
  be stored in, each carrying its own `to_default`. This is which spellings are legal, and
  the factor from each to its quantity's `default_unit` — the source of the Allowed units
  table above.

**`to_default` is the multiplier from a value in `unit` into that quantity's
`default_unit`** — `0.001` for `kW` means "multiply by 0.001 to get `MW`." Two values are not
ordinary multipliers:

- **`null`** — the unit is relative, not absolute: a `pu` row. There is no fixed factor,
  because the physical value depends on a base recorded elsewhere, resolved through the
  annotated field's own discriminator and the blob's own `base_power` (see
  [the decision procedure](#reading-a-value-the-decision-procedure)) — never a constant in
  this file.
- **`0.0`** — `Frequency`/`Hz`'s row; this layer deliberately performs no conversion at all
  (see the note under [Allowed units and conversions](#allowed-units-and-conversions)).

**Every `x-unit`, and every value inside an `x-units` map, in every schema file, must name a
unit that is an `allowed_units` row for that field's quantity type** (or the literal `"pu"`)
— `scripts/validate_units.py` enforces this and rejects anything else. `Core/units.json` is
the only place a new unit spelling may be introduced; see
[`docs/UNIT_ANNOTATIONS.md`]({UNIT_DOCS_URL}) for how — that page covers authoring an
annotation, a different job from reading one.
"""


def render_units_example_table():
    """The worked example's rows, rendered from the bundled spec with the same
    render_unit used on the type pages, so the example matches the real page."""
    bundled, _ = load_domain(UNITS_EXAMPLE_DOMAIN)
    definitions = bundled.get("$defs", {})
    schema = resolve(bundled["components"]["schemas"][UNITS_EXAMPLE_TYPE], definitions)
    lines = [
        f"| `{UNITS_EXAMPLE_TYPE}` property | Unit | Description |",
        "|---|---|---|",
    ]
    for pname in UNITS_EXAMPLE_PROPERTIES:
        pnode = schema["properties"][pname]
        desc = first_sentence(resolve(pnode, definitions).get("description"))
        lines.append(f"| `{pname}` | {render_unit(pnode)} | {desc} |")
    lines.append("")
    return lines


def units_page():
    validate_worked_example()
    units = load_json(UNITS_JSON)
    lines = front_matter(
        [
            ("title", "Units"),
            ("layout", "default"),
            ("nav_order", 2),
        ]
    )
    lines += ["# Units", "", UNITS_LEAD, UNITS_ROUTING, UNITS_MODEL, UNITS_QUANTITY_TYPES_LEAD]
    lines += render_quantity_types_table(units)
    lines.append(UNITS_ALLOWED_LEAD)
    if any(entry["to_default"] == 0.0 for entry in units["allowed_units"]):
        lines.append(UNITS_ALLOWED_ZERO_NOTE)
    lines.append(UNITS_ALLOWED_FREQUENCY_NOTE)
    lines += render_allowed_units_table(units)
    lines.append(UNITS_READING_JSON)
    lines.append(UNITS_CONVENTIONS)
    lines.append(UNITS_WORKED_EXAMPLE)
    lines.append(
        UNITS_READING_LEAD.format(
            type_name=UNITS_EXAMPLE_TYPE,
            type_page=f"reference/{UNITS_EXAMPLE_DOMAIN}/{UNITS_EXAMPLE_TYPE}.md",
        )
    )
    lines += render_units_example_table()
    lines.append(UNITS_READING_TAIL)
    lines.append(UNITS_AUTHORS)
    return "\n".join(lines).rstrip("\n") + "\n"


def build_nav():
    lines = ["- Units: units.md", "- Reference:", "    - Overview: reference/index.md"]
    for domain in DOMAINS:
        meta = PACKAGE_META[domain]
        bundled, _ = load_domain(domain)
        type_names = sorted(bundled["components"]["schemas"].keys())
        lines.append(f"    - {meta['label']}:")
        lines.append(f"        - Overview: reference/{domain}/index.md")
        for name in type_names:
            lines.append(f"        - {name}: reference/{domain}/{name}.md")
    return "\n".join(lines)


def write_tree(out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    domain_info = {}
    for domain in DOMAINS:
        bundled, source_paths = load_domain(domain)
        definitions = bundled.get("$defs", {})
        schema_names = set(bundled["components"]["schemas"].keys())
        known_schemas = build_known_schemas(bundled, definitions)
        domain_dir = out_dir / domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        for name in bundled["components"]["schemas"]:
            schema = resolve(bundled["components"]["schemas"][name], definitions)
            page = type_page(
                name, domain, schema, definitions, known_schemas, schema_names, source_paths[name]
            )
            (domain_dir / f"{name}.md").write_text(page)
        unbundled = load_json(REPO_ROOT / f"openapi-{domain}.json")
        info_description = unbundled["info"]["description"]
        domain_info[domain] = info_description
        index_page = package_index_page(domain, bundled, definitions, schema_names, info_description)
        (domain_dir / "index.md").write_text(index_page)
    (out_dir / "index.md").write_text(root_index_page(domain_info))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="docs/reference", help="Output directory (default: docs/reference)"
    )
    parser.add_argument(
        "--units-out",
        default="docs/units.md",
        help="Path for the units page (default: docs/units.md)",
    )
    parser.add_argument(
        "--nav", action="store_true", help="Print the YAML nav fragment to stdout and write nothing"
    )
    parser.add_argument("--clean", action="store_true", help="Remove --out first")
    args = parser.parse_args()

    if args.nav:
        print(build_nav())
        return 0

    out_dir = Path(args.out)
    units_out = Path(args.units_out)
    if args.clean:
        if out_dir.exists():
            shutil.rmtree(out_dir)
        units_out.unlink(missing_ok=True)
    write_tree(out_dir)
    units_out.parent.mkdir(parents=True, exist_ok=True)
    units_out.write_text(units_page())
    return 0


if __name__ == "__main__":
    sys.exit(main())
