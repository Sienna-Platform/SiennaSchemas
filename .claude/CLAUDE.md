# SiennaSchemas — Claude Guide

The **single source of truth for Sienna data-model schemas**: hand-written JSON Schema (draft-07) definitions organized by domain, the OpenAPI specs that select what each generated package contains, the **unit vocabulary** (`Core/units.json`), and the units validator. Everything here is source; the generated side lives downstream. Platform conventions: the `sienna-psy6` skill. The end-to-end pipeline and its gates are documented in `docs/PIPELINE.md`; the annotation spec is `docs/UNIT_ANNOTATIONS.md`.

Branch and repo state change constantly — read them, don't trust them written down: `git branch --show-current`, `git status --short`, `git log --oneline origin/main..HEAD`.

## Working agreement

**Responses.** Keep responses focused and concise. Spend most of the response on the main answer and keep caveats short. When asked to explain something, give a high-level summary unless an in-depth explanation is requested.

**Scope.** Deliver what was asked, at the scope intended. Make routine judgment calls yourself, and check in only when different readings of the request would lead to materially different work. If the request seems mistaken or a better approach exists, say so in a sentence and continue with the task as asked rather than quietly narrowing, widening, or transforming it. Finish the whole task, and stop short of actions clearly beyond what was asked. Two concrete limits here: the PSY ⇄ OpenAPI ⇄ GridDB converter does not exist — don't improvise a partial one; and generated downstream packages are outputs, so fix their inputs, never hand-edit them.

**Communication.** Before the first tool call, say in one sentence what you're about to do. While working, give a brief update only when you find something important or change direction. When finishing, lead with the outcome: the first sentence answers "what happened" or "what did you find", with supporting detail after it.

**Corrections.** Only correct an earlier statement when the error would change the user's code, conclusions, or decisions. State the correction plainly and briefly, then continue. For slips that change nothing, make the fix and move on without noting it.

**Verification.** `docs/PIPELINE.md` lists the five gates and what each one catches — run those; they are real build steps, not self-checking. Beyond them, don't add verification passes or re-check work already checked.

**Reviews and audits.** When reviewing schemas or auditing drift, report everything found and filter in a separate pass. Pre-filtering to "high severity only" suppresses real findings.

**Written output.** Match document length to what the task needs: cover the substance without filler sections, redundant summaries, or boilerplate.

**Delegation.** Use subagents only when explicitly requested. When they are, delegate only genuinely independent, sizeable tracks — a wide sweep across the whole domain schema tree, say — never to verify your own work, and keep spawn counts low.

## Multi-repository pipeline

```
SiennaSchemas (this repo — hand-written source)
  ├─ openapi-*.json ──datamodel-codegen──▶ power-openapi-models   (Python / pydantic v2)
  ├─ openapi-*.json ──openapi-generator──▶ PowerOpenAPIModels     (Julia packages)
  └─ Core/units.json ──SiennaGridDB/scripts/generate_unit_registry.py──▶ GridDB sealed unit registry
```

- Schemas mirror **PowerSystems.jl component types field-for-field** but in natural units with integer-id references (PSY internals are per-unit with object references). A PSY field change without a schema change is drift; `scripts/check_psy_parity.py` is the gate that catches it.
- No converter exists between a PSY `System` and these types or GridDB rows, so **validators are the only consistency mechanism** — treat annotation and vocabulary correctness as load-bearing.
- Component coverage against PSY is complete for everything except Dynamics: every non-dynamics PSY struct has a schema, and the absent structs are all in the dynamics family (AVR, TurbineGov, Machine, PSS, DynamicInjection, filters, limiters), deferred by design. Fields absent **by convention rather than drift**: `services`/`reserves`/`contributing_services` (many-to-many), `n_states`/`states`/`states_types` (read-only dynamic metadata), and `TransformerCircuit.base_value` (derived units anchor PSY marks "do not modify"). Don't re-flag these as gaps.
- **Known gap:** Service components exist, but no schema records *which devices contribute to which service* — there is no membership type here, unlike `Core/SupplementalAttributes/SupplementalAttributeAssociation.json`. The DB side of the same gap is noted in SiennaGridDB.

## Generated package membership

Four packages (each generated in both Julia and Python):

| Package | Contents | Dependencies |
|---|---|---|
| **PowerCoreOpenAPIModels** | Shared types: curves, enums, helpers (MinMax, UpDown, CostCurve, FunctionData, …) | None |
| **PowerOperationsOpenAPIModels** | Topology, Branch, StaticInjection, Service | Core |
| **PowerInvestmentsOpenAPIModels** | Technologies, Financials, Requirements, Attributes, Regions, Portfolio | Core |
| **PowerDynamicsOpenAPIModels** | DynamicGeneratorComponent, DynamicInverterComponent | Core |

Investments depends only on Core (not Operations); integer ID cross-references are semantic, not formal type dependencies.

## Directory structure

```
Core/                    # common.json (shared types), units.json (unit vocabulary),
                         # TimeSeries/, SupplementalAttributes/
Operations/              # Topology/, Branch/, StaticInjection/, Service/, SupplementalAttributes/
Investments/             # Technologies/, Financials/, Requirements/, Attributes/, Regions/, Portfolio/
Dynamics/                # DynamicGeneratorComponent/, DynamicInverterComponent/
openapi-{core,operations,investments,dynamics}.json     # $ref wrappers selecting package membership
openapi-config-*.json    # generator configs (inlineSchemaNameMappings)
scripts/                 # validate_units.py, bundle_specs.py,
                         # check_psy_parity.py, check_psip_parity.py
dist/                    # bundled specs for codegen consumers (gitignored)
docs/                    # PIPELINE.md (gates), UNIT_ANNOTATIONS.md (annotation spec)
.github/workflows/       # release.yml, validate-schemas.yml
```

Cross-references are relative paths (`"$ref": "../../Core/common.json#/definitions/MinMax"`) — **directory layout is load-bearing**; moving a file breaks every `$ref` to it.

## Unit vocabulary and annotations

`Core/units.json` is the vocabulary and the single source of truth, consumed by the validator here and by SiennaGridDB's registry generator. The full annotation spec is `docs/UNIT_ANNOTATIONS.md` — read it before adding an annotation form; the rules below are the parts most often needed.

- Every numeric property carries `x-unit`, or `x-units` + `x-unit-discriminator` for discriminated cases (`x-unit-base` names a sibling property). Values must appear in `allowed_units` or be the literal `"pu"`.
- For the four branch-impedance quantities (Resistance/Reactance/Susceptance/Conductance), `pu` **is** a registered vocabulary unit — one of **two** discriminated storage options, with GridDB recording which per row (`DEVICE_BASE` → `pu`, `NATURAL_UNITS` → `ohm`/`S`). Per `docs/UNIT_ANNOTATIONS.md` these branch parameters are the *only* deliberate exception to natural-unit storage; everywhere else `"pu"` is a model-layer annotation, never a stored unit.
- **`SYSTEM_BASE` is not a valid unit basis.** Every basis enum offers exactly `NATURAL_UNITS` and a per-unit option: `DEVICE_BASE`, per-unit on the component's own recorded base — components whose per-unit data was historically on the system base record that base in their own `base_power` (`base_current` for `TModelHVDCLine`, which per-unitizes against a current). Shunts use `DEVICE_MVAR` (a power at unity voltage, **not** a respelling of `DEVICE_BASE`) via `ShuntAdmittanceUnitBasis`, which omits `DEVICE_BASE` because a shunt has no device MVA rating.
- Reactive power is `MVAr`, never `MW`. Percent is banned — store fractions.
- **Time is three quantity types, each with exactly one allowed unit so the vocabulary enforces the tier:** `Duration` (`s`) for continuous time constants; `OperationalDuration` (`min`) for scheduling and commitment durations; `CalendarPeriod` (`yr`) for planning spans. The tier fixes the unit, not the numeric type — `units.json` owns the integrality policy. Hours are not a vocabulary unit — an hours-valued field is a tier error, not a conversion. `CalendarPeriod` carries no `to_default` bridge on purpose: a calendar year is not a fixed number of seconds.
- Durations reuse the real-valued shared structs (`MinMax`/`UpDown`/`TurbinePump`/`StartUpStages`) with `x-unit: min`; there is no parallel integer struct family.
- Only dependency is `jsonschema`. `validate_units.py --fix-descriptions` regenerates the canonical `Units:` sentences; `--check-descriptions` is the CI gate.
- **The `x-unit-quantity` pairing rule needs a GridDB checkout** (`--griddb-path`, default `../SiennaGridDB`) and is silently skipped when absent — see the `load_column_allowed_units` docstring in `scripts/validate_units.py` for the full behavior. CI has no GridDB checkout, so a green CI run is not evidence the pairing rule is satisfied.
- Remaining annotation debt: Dynamics numerics (deferred by design) and the sync-check warnings from GridDB, which are gaps rather than contradictions — GridDB's README defines that distinction.

## Schema conventions

- JSON Schema draft-07 (`"$schema": "http://json-schema.org/draft-07/schema#"`).
- Discriminated unions: `oneOf` + a `discriminator` block (`propertyName` + `mapping`).
- Component base properties: `id` (integer), `name` (string), `available` (boolean).
- `ext`, supplemental attributes, and many-to-many relations are stored *separately*; one-to-many relations become integer id references, named with an `_id` / `_ids` suffix.
- Avoid read-only and derived fields — PSY has them, this layer does not.
- Property ordering follows Sienna conventions: id, name, bus, …
- Path-aliasing collisions are resolved via `inlineSchemaNameMappings` in `openapi-config-*.json` (workaround for [openapi-generator #18948](https://github.com/OpenAPITools/openapi-generator/issues/18948)). The alias count is empirical — confirm it by running codegen, not by reasoning about consumer counts.

## Generator configs & local codegen check

```bash
openapi-generator generate -c openapi-config-core.json -g julia-server -o ./PowerCoreOpenAPIModels.jl
```

Config files are language-agnostic; pick `-g python` or `-g julia-server` on the command line. Production Julia generation happens in the PowerOpenAPIModels repo via `julia-client` plus its `reorganize.jl` and needs Docker with a codegen image; the Python side uses datamodel-codegen. The two toolchains fail in different ways, so a change that generates cleanly in one is **not** proven in the other — regenerate both. Each downstream repo documents its own environment setup.

## Recipe: change a schema (end-to-end)

1. Edit the domain JSON; keep `$ref` paths valid — layout is load-bearing.
2. Annotate numerics with `x-unit` from `Core/units.json`.
3. Run the gates in `docs/PIPELINE.md`, then rebundle with `bundle_specs.py` → `dist/` (units ride as `Units:` sentences for generators that drop vendor extensions).
4. If the change mirrors a PSY descriptor change, confirm both sides. If it affects DB columns, coordinate `SiennaGridDB/schema/column_conventions.json` and registry regeneration — **tag a Schemas release before GridDB regenerates**.
5. Regenerate both model packages (`make generate && make validate` in each). Codegen breakage is cheapest to catch here.
6. Tag a release when the change set is coherent.

## Creating a release

```bash
git tag v1.0.0
git push origin v1.0.0
```

The tag push triggers the GH Actions release workflow (schema tarball → GitHub Release). Downstream repos poll for releases and record the consumed tag in their `.schema-version` — check a downstream repo to see whether the loop has fired for a given tag.

<tone_preference>
Keep outputs reasonably concise. Lead with the outcome, and let the pipeline gates stand in for extra verification passes.
</tone_preference>
