# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project is pre-1.0; any release may change schemas incompatibly.

## [0.1.0] - 2026-09-12

First release. Definitions for the power system data model, in six groups — grid topology and
equipment, costs and curves, investment planning, machine dynamics, time series, and the shared
basics — each generated as its own package for Python, Julia, and SQL.

### Added

- `NonSequentialTimeSeries.timestamps_uri` — a locator for the series' explicit time axis, the
  counterpart of `uri` for the values. Optional, so a producer that predates it stays valid, and
  present on no other type.

  It exists so an irregular series can be restored from a document. Without it the document says
  which values a row has but not which of the store's time axes they sit on, and the store cannot
  supply the answer: arrays are content-addressed, so two irregular series with byte-identical
  values on different axes share one stored array and only the store's own `timestamps_hash`
  distinguishes them. A locator rather than the vector itself because the axis is shared — a
  cohort names it once each, where inlining the timestamps would repeat the whole vector per row.

### How to consume it

The release tarball ships the schema tree as authored: the domain schemas, `Core/units.json`,
and the `openapi-<domain>.json` selectors. There is nothing to build first — both codegen
toolchains resolve the `$ref` graph across files themselves, selectors included. A consumer
records the tag it generated from in its own `.schema-version`.

Every numeric property carries its unit twice: as an `x-unit` annotation, and as a canonical
`Units:` sentence at the end of its description. The sentence exists because neither toolchain
renders vendor extensions, so it is the only channel that survives into generated code.

### Known limitations

- **Dynamics is a sample, not yet a model of the domain.** Seven component schemas: one
  machine, one turbine governor, one excitation system, and four renewable-inverter
  controllers. The rest of the family is deferred by design, and dynamics supertypes are
  excluded from the PowerSystems.jl parity gate.
- **Investments diverges from PowerSystemsInvestmentsPortfolios.jl.** `Node` and `Zone` have no
  schema; `RequirementAssociation` has no matching struct; several technology types differ in
  fields and units. The parity gate reports this and is deliberately non-blocking.
- **The unit-quantity pairing rule needs a SiennaGridDB checkout** and skips silently without
  one, so a green CI run is not evidence that it holds.

[0.1.0]: https://github.com/Sienna-Platform/SiennaSchemas/releases/tag/v0.1.0
