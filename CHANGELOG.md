# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project is pre-1.0; any release may change schemas incompatibly.

## [Unreleased]

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

## [0.1.0] - 2026-08-31

First release. Definitions for the power system data model, in six groups — grid topology and
equipment, costs and curves, investment planning, machine dynamics, time series, and the shared
basics — each generated as its own package for Python, Julia, and SQL.

[0.1.0]: https://github.com/Sienna-Platform/SiennaSchemas/releases/tag/v0.1.0
