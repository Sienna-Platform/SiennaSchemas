---
title: Home
layout: default
nav_order: 1
---

# SiennaSchemas

Machine-readable definitions of the things in an electric power system — buses, generators,
transmission lines, loads, storage, costs, and time series. Each definition describes the exact
shape of one kind of data, so that tools written in different languages agree on it: a value that
means megawatts in one language means megawatts in every other, and a required field in one is
required everywhere.

These pages are the type reference generated from those definitions. For what the project is,
how to use it, and how it fits into the rest of the pipeline, see the
[README](https://github.com/Sienna-Platform/SiennaSchemas#readme).

## What's here

The definitions are organized into six groups, each generated as its own package so a project
can take only the parts it needs:

- **Grid topology and equipment** — buses, areas and zones, lines and transformers, generators,
  loads, storage, and services such as reserves.
- **Costs and curves** — cost and value curves, fuel and technology enumerations, and other
  pieces the grid and investment groups share.
- **Investment planning** — candidate technologies, financial data, requirements, regions, and
  portfolios.
- **Machine dynamics** — dynamic generator and inverter models.
- **Time series** — six ways of attaching a series of values to a component: single,
  non-sequential, deterministic, deterministic-single, probabilistic, and scenarios.
- **Shared basics** — unit systems, function-data shapes, value pairs (`MinMax`, `UpDown`,
  `FromTo`, …), geographic information, and data provenance. This group carries nothing specific
  to power systems, so software with no power-system concepts in it can depend on it alone.

Browse the full type list in the [Reference](reference/index.md) section, or use search above
to jump straight to a type. Start with [Units](units.md), which explains how a component blob
is self-contained and read on its own, the vocabulary of quantities and units every numeric
field draws on, and the conventions those fields follow.

## Further reading

- [PIPELINE.md](https://github.com/Sienna-Platform/SiennaSchemas/blob/main/docs/PIPELINE.md) —
  how these definitions turn into generated code and a database schema.
- [UNIT_ANNOTATIONS.md](https://github.com/Sienna-Platform/SiennaSchemas/blob/main/docs/UNIT_ANNOTATIONS.md) —
  for writing or changing a schema: how a unit annotation is declared, placed, and validated.
  To interpret a unit you are reading, see [Units](units.md) instead.
