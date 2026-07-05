# SiennaSchemas

JSON Schema definitions for power system data models. This repository is the single source of truth for data structures used across the Sienna ecosystem.

## How It Works

Tagged releases (e.g., `v1.2.0`) trigger a GitHub Actions workflow that packages all schemas into a tarball and publishes a GitHub Release. Downstream repositories poll for new releases and automatically regenerate their artifacts using their own language-specific codegen containers.

## Downstream Repositories

| Repository | What It Generates |
|---|---|
| [power-openapi-models](https://github.com/Sienna-Platform/power-openapi-models) | OpenAPI spec + Python/Pydantic models |
| [PowerOpenAPIModels.jl](https://github.com/Sienna-Platform/PowerOpenAPIModels.jl) | Julia type definitions |
| [SiennaGridDB](https://github.com/Sienna-Platform/SiennaGridDB) | SQL DDL migrations (PostgreSQL) |

Each downstream repo contains its own `codegen/` directory with a Dockerfile and generation script tailored to its target language. When a new schema release is detected, the repo's codegen container runs and a pull request is opened for review. Repos track which schema version they were last generated from in a `.schema-version` file.

## Code Generation for Testing

Use `openapi-generator` with the provided config files:

```bash
# Julia
openapi-generator generate -c openapi-config-core.json \
  -g julia-server \
  -o ./PowerCoreOpenAPIModels.jl

# Python
openapi-generator generate -c openapi-config-core.json \
  -g python \
  -o ./power_core_openapi_models
```

Replace `core` with `operations`, `investments`, or `dynamics` for other packages.

## Creating a Release

```bash
git tag v1.0.0
git push origin v1.0.0
```

This triggers the release workflow, which publishes the schema tarball. Downstream repos will pick up the new version on their next polling cycle (every 6 hours) or via manual workflow dispatch.
