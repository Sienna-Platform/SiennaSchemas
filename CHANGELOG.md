# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project is pre-1.0; any release may change schemas incompatibly.

## [Unreleased]

## [0.3.0] - 2026-10-26

0.3.0 starts a new compatibility line. Documents written at 0.2.x do not read at 0.3.0;
regenerate them with a current producer, including cached test systems.

### Added

- `SeriesCompensator`, a branch with one series reactance: a series capacitor or reactor, a
  thyristor-controlled or a static synchronous series compensator. Its `control_mode`
  (`SeriesCompensatorControlMode`) is `FIXED`, `BYPASSED`, `DISPATCHABLE` or
  `ACTIVE_POWER_FLOW`; the last two require `reactance_limits`, and `ACTIVE_POWER_FLOW` also
  `active_power_flow_setpoint`.
- `CurrentLossCurve`, a loss curve of active power against current (MW against kA in natural
  units).
- `IntervalAlignment` (`PERIOD_BEGINNING`, `PERIOD_MIDPOINT`, `PERIOD_ENDING`) and an optional
  `interval_alignment` (default `PERIOD_ENDING`) on `SingleTimeSeries`, `Deterministic`,
  `DeterministicSingleTimeSeries`, `Probabilistic`, and `Scenarios`. The value is record-only.
- `Area.scheduled_net_interchange` (nullable, follows `power_units`; positive means export) and
  `Area.out_of_area_load_ids`.
- `InterconnectingConverter.rated_dc_voltage` and `rated_ac_voltage` (kV, default 0.0 =
  unspecified).
- An optional `license` on `DataSource`: an SPDX identifier, a license name, or a URL.
- The release workflow also sends `schema-release` to SiennaGridDB.

### Changed

- `XY_Coords` is renamed to `TwoDimensionalFunctionPoint`. The old name was easy to confuse with
  geographic coordinates. Consumers that name the generated type must use the new name.
- `FuelCurve.fuel_units` is required. Its type is the new `FuelUnit` enum (`MMBTU`, `GJ`), and
  `fuel_cost` is priced per one `fuel_units`.
- `InterconnectingConverter.loss_function` is a `CurrentLossCurve`, not a `LossCurve`. The wire
  shape does not change, but natural-units coefficients now mean MW against kA.
- Seven DC current fields are in kA, not A: `InterconnectingConverter.dc_current` and
  `max_dc_current`, `TwoTerminalVSCLine.dc_current`, `max_dc_current_from` and
  `max_dc_current_to`, `TwoTerminalLCCLine.current_transfer_setpoint`, and
  `TModelHVDCLine.base_current`. An old value still validates but reads 1000 times too large.
  The "no limit" default of the three `max_dc_current*` fields changes from `1e8` to `9999.0`.
- `HydroTurbine.efficiency` and `HydroPumpTurbine.efficiency` are annotated as a `Fraction`
  (unit `1`).
- `interval_alignment` is a reserved time series feature name.

## [0.2.0] - 2026-10-05

### Added

- The full dynamic generator and dynamic inverter component families. The files are in one
  subfolder per role (`AVR/`, `Machine/`, `PSS/`, `Shaft/`, `TurbineGov/`, `Converter/`,
  `DCSource/`, `Filter/`, `FrequencyEstimator/`, `InnerControl/`, `OuterControl/`,
  `OutputCurrentLimiter/`).
- `OperationalFlowLimit` and an optional `operational_flow_limit` on `Line`,
  `DiscreteControlledACBranch`, `TransformerCircuit`, `GenericArcImpedance`, and the three
  two-terminal HVDC lines.
- `switching_times` on `ThermalStandard` and `ThermalMultiStart`.
- Shared voltage control: `VoltageControlAssociation`, `ReactivePowerSharing`, and
  `VoltageDroopControl`.
- Unit annotations for cost and loss curves (`x-curve-axes`, `x-curve-output`,
  `x-curve-dimension`).
- `check_refs.py` checks that each tagged `oneOf` variant pins its discriminator with `const`.
- The release workflow sends `schema-release` to the model-package repositories.
- `schema_version` on `SystemDocument` and `PortfolioDocument`, and `docs/VERSIONING.md`
  defining the compatibility line and the reader rule.

  A document had no record of the schema that wrote it, so a reader built from an older release
  either dropped the newer rows silently or failed at an unrelated place with `extra field`.
  The stamp lets a reader compare versions before decoding and refuse with a message naming
  both. `scripts/check_compat.py` classifies the schema diff since the last tag, so the version
  number reflects whether documents stay readable.

### Changed

- `schema_version` is required on both documents. Documents written before this change have no
  stamp and must be regenerated with a current producer.
- A field whose unit changed with a mode enum is now one field per physical quantity, each with
  a fixed unit. For example, `TransformerCircuit.control_limits` and
  `controlled_quantity_limits` become five `*_limits` bands.
- HVDC impedance, voltage, and voltage-droop fields are natural units only. `parameter_units`
  and `dc_voltage_units` are removed.
- The HVDC lines lose `active_power_limits_from`/`_to`. The generic and LCC lines gain
  `rating`, `rating_from`, and `rating_to`. `TransformerCircuit.rating` is required.
- Existing dynamics files moved into the role subfolders, so their `$ref` paths changed.

### Removed

- `Operations/Branch/MonitoredLine.json`. Use `Line.operational_flow_limit`.
- `GenericArcImpedance.max_flow`. Use `operational_flow_limit`.
- `scripts/check_psy_parity.py` and its CI step. The gate compared the schemas against a
  PowerSystems.jl branch, so each schema change needed a matching PowerSystems.jl branch. It
  did not show whether a release breaks PowerSystems.jl (issue #72).
- `Investments/SupplementalAttributes/TopologyMapping.json`, and its entry in the
  `openapi-investments` selector.

  It stored "the mapping between a zone and the associated buses in the base system" — and
  neither half of that is the portfolio's to record any more. A portfolio has no `Zone`: the
  schema never defined one, and `PowerSystemsInvestmentsPortfolios.jl` is removing its own
  `Node`/`Zone` in favour of using the base system's topology directly. The buses are in the
  base system, where each `ACBus` already names its `area`, so a `TopologyMapping` restated
  that membership in a second place nothing kept in sync.

  It was also the only supplemental attribute that would have had to describe a component in
  a *different* document. `PowerOpenAPIModels.jl`'s `add_supplemental_attribute!` rejects a
  component id that is not in the document it is adding to, so the attribute had no
  attachable target once `Zone` was gone.

  Nothing produced it: the RTS-GMLC tutorials emit none in either language, and the PSIP
  parity gate now reports `TopologyMapping` alongside `Node` and `Zone` as PSIP-side types
  pending removal.

### Fixed

- `validate_units.py --fix-descriptions` is idempotent on a `Units:` sentence with no final
  period. It appended a second sentence before.

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

[Unreleased]: https://github.com/Sienna-Platform/SiennaSchemas/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Sienna-Platform/SiennaSchemas/releases/tag/v0.3.0
[0.2.0]: https://github.com/Sienna-Platform/SiennaSchemas/releases/tag/v0.2.0
[0.1.0]: https://github.com/Sienna-Platform/SiennaSchemas/releases/tag/v0.1.0
