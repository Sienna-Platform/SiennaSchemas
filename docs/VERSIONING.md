# Schema versioning and compatibility

A `SystemDocument` or `PortfolioDocument` records the schema version that wrote it. A reader
compares that version with its own, before decoding, and either reads the document, upgrades it,
or refuses with a message naming both versions. `scripts/check_compat.py` makes the version number
trustworthy, so "same line" means "readable".

The reference implementation of the rule is `scripts/schema_version.py` (`parse`, `line`,
`classify`). The shared test vectors are `tests/fixtures/versioning/cases.json`; every binding
must agree with them.

## Compatibility line

There is one version for the repository: the release tag, equal to `info.version` in all six
`openapi-*.json` selectors.

The **compatibility line** is `MAJOR` for versions >= 1.0 and `0.MINOR` for 0.x (the Julia Pkg and
Cargo rule). Within a line, the next component (`MINOR` from 1.0, `PATCH` in 0.x) is the
**feature** bump. Everything that changes what a valid document means starts a new line.

## The stamp

`schema_version` is the first property of both documents and is required. It matches
`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$`. It has no `const`: a
`const` would make every older document invalid against a newer schema.

A writer stamps its own schema version with the leading `v` removed. A local regeneration stamps
`git describe` output (for example `0.1.0-3-gabc123-dirty`), which parses as a prerelease.

## Reader rule

`R` is the reader's version and `D` is the document's. Run the rule on the raw parsed JSON,
before decode; otherwise a binding that rejects unknown keys reports `extra field` before the
version check runs. The first matching row wins.

| Condition | Outcome | Reader does |
|---|---|---|
| `schema_version` absent | `missing` | error: the document predates versioning; re-export it with a current producer |
| does not match the pattern | `malformed` | error naming the value |
| `D` or `R` is a prerelease and `D` is not `R` | `incompatible` | error: dev builds read only their own output |
| `line(D)` differs from `line(R)` | `incompatible` | error naming both versions; migration between lines is not the reader's job |
| `D > R` | `newer` | error: written by schema `D`, reader understands up to `R`; update the model package |
| `D < R` | `upgradable` | read normally; defaults from newer feature releases apply |
| `D = R` | `current` | read normally |

Versions compare by `(major, minor, patch)` only. Every outcome except `upgradable` and
`current` is an error, never a warning, and there is no lenient mode.

The check lives in one place per binding, the shared raw-dict read path, and is exposed as
`check_schema_version(raw) -> outcome`.

## Upgrading and writing

Writers always stamp `R`, so reading and then writing upgrades a document. The gate guarantees
that every document valid at `D` is valid at `R`, so no per-version migration code exists.

Every binding exposes the same four operations, spelled in its own idiom:

- `check_schema_version(raw) -> outcome`: the reader rule, on the raw parsed JSON.
- `write_document(doc, path; schema_version = current | source)`: the write target below.
- `get_source_schema_version(doc)`: the version the document was read at (`D`), `R` for a new
  document.
- `upgrade_document(src, dst; force)`: `read_document` followed by `write_document`; only
  `current` and `upgradable` succeed.

The write target is chosen at save time:

- `current` (default): stamp `R`. It cannot fail.
- `source`: keep `D`. The container records it as `source_schema_version` (set to `D` on read,
  `R` for a new document; never serialized).

`source` with `D = R` is the same as `current`. With `D < R`, the writer validates the encoded
tree against `D`'s strict bundle (one self-contained draft-07 schema in which every object schema
without an explicit `additionalProperties` gets `false`, built by `scripts/build_bundles.py`). If
anything is unknown to `D`, the write fails and the error lists every offending path, not just the
first. Nothing is dropped to make the write fit. Without a validator installed the write is an
error, never a fallback to `current`.

For validation to be enough, writers use **canonical encoding**: omit an optional property that is
absent, null, or equal to its schema `default`. This is safe because the gate forbids changing a
`default` within a line.

Strict bundles ship in the release tarball as
`schemas/bundles/<version>/{SystemDocument,PortfolioDocument}.json`, for every release in `R`'s
line up to `R`. The shared vectors ship as `schemas/versioning/cases.json`.

### Spellings per binding

| Binding | Check / upgrade / source version | Write target | Error | Source validation needs |
|---|---|---|---|---|
| Julia | `check_schema_version`, `upgrade_document`, `upgrade_portfolio_document`, `get_source_schema_version` | `write_document(...; schema_version = :current \| :source)` | `SchemaVersionError` | `InfrastructureCoreOpenAPIModels`' JSONSchema.jl extension |
| Python | `check_schema_version`, `upgrade_document`, `upgrade_portfolio_document`, `get_source_schema_version` | `write_document(..., schema_version="current" \| "source")` | `SchemaVersionError` | `power-openapi-models[source-version]` |
| TypeScript | `checkSchemaVersion`, `upgradeDocument`, `upgradePortfolioDocument`, `getSourceSchemaVersion` | `writeDocument(doc, path, { schemaVersion: "current" \| "source" })` | `SchemaVersionError` | optional peer `ajv` (Node only) |
| Rust | `check_schema_version`, `upgrade_document`, `upgrade_portfolio_document`, `get_source_schema_version` | `SchemaVersionTarget::{Current, Source}` | `DocumentError` | `source-version` feature |

`read_document` and `upgrade_document` act on a `SystemDocument`; `read_portfolio_document` and
`upgrade_portfolio_document` are their `PortfolioDocument` counterparts (TypeScript:
`readPortfolioDocument`, `upgradePortfolioDocument`).

In Julia the version check, `write_document`, and `get_source_schema_version` live in
`InfrastructureCoreOpenAPIModels`; `SystemDocument` and its readers in `PowerCoreOpenAPIModels`;
`PortfolioDocument` and its readers in `PowerInvestmentsOpenAPIModels`. Neither container needs
the `PowerOpenAPIModels` umbrella.

A document whose root is not a JSON object is a format error, not `missing`: the public check
raises the binding's document format error (Julia `DocumentFormatError`, Python pydantic
`ValidationError`, TypeScript `ZodError`, Rust `Err(DocumentError::Invalid)`) instead of returning
an outcome.

A source write that fails validation lists every offending path, deduplicated and sorted, as a
JSON Pointer (`/components/ACBus/0/foo`). Keys are not escaped per RFC 6901, so the paths are a
diagnostic to read, not a contract to parse.

## Canonical messages

Each error outcome has one text; every binding uses it verbatim, with `{reader}` and `{document}`
replaced by `R` and `D`. `scripts/schema_version.py` `message()` is the source.

| Outcome | Text |
|---|---|
| `missing` | `document has no schema_version: it predates versioning; re-export it with a current producer (psy5 bundles: PowerSystemsUpdater)` |
| `malformed` | `document schema_version {document} is not a valid version` (`{document}` is the offending value as compact JSON with raw Unicode, no spaces: `"v0.1.0"`, `0.1`, `null`, `true`, `[0,1,0]`, `{"v":"0.1.0"}`) |
| `incompatible`, prerelease | `document written by schema {document} cannot be read by schema {reader}: dev builds read only their own output` |
| `incompatible`, line | `document written by schema {document} (line {document_line}) cannot be read by schema {reader} (line {reader_line}): documents do not cross compatibility lines; migrating between lines is a separate upgrade tool's job (psy5 bundles: PowerSystemsUpdater)` |
| `newer` | `document written by schema {document}; this reader understands up to {reader}; update the model package to one built from >= {document}` |

A line is written `MAJOR` from 1.0 and `0.MINOR` for 0.x.

## The gate

`scripts/check_compat.py` answers: is every document valid under the base tag still valid under
HEAD, with the same meaning? It diffs the resolved schemas against the base tag
(`git describe --tags --abbrev=0 --exclude "*-*"` by default, so a prerelease tag is never the
base) and fails closed: any change to a keyword it does not list as a feature counts as breaking.

- Feature, allowed within a line: new schema or property (not required), property dropped from
  `required`, widened `type`, added `enum` value, added discriminator `mapping` entry, relaxed
  bound, widened `additionalProperties`, a type added to a document's components or attributes.
  An added or widened `oneOf` branch is a feature only when every branch const-pins and requires
  the same tag property (the strict bundles drop `discriminator`); `anyOf` branches always are.
- Breaking, needs a new line: removed or renamed schema or property, property added to `required`,
  narrowed or changed `type`, retargeted `$ref`, removed `enum` value, removed `oneOf` or `anyOf`
  branch, removed or retargeted discriminator `mapping` entry, changed `discriminator.propertyName`,
  `const` added or changed, tightened bound, `pattern` added or changed, narrowed
  `additionalProperties`, a `default` added, changed or removed, any `x-unit*` change. Also
  breaking: closing an object the strict bundle left open (its first property, or dropping an
  explicit `additionalProperties: true`), a type leaving a document's components or attributes,
  and an added or widened `oneOf` branch in a union that is not tag-pinned as above.
- Ignored: `description`, `title`, `examples`, `$comment`, key order.

All six selectors must carry the same `info.version`. When the version equals the base tag's, the
gate reports the required next version and exits 0. When it differs, the gate fails if the version
understates the changes.
