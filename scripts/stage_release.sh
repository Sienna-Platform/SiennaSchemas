#!/usr/bin/env bash
# Stage the release tree into <dir>: the tree the release tarball ships, and the tree the
# model-build gate generates from. Both use this script, so the gate tests what ships.
#
# Usage: scripts/stage_release.sh <dir>
#
# <dir> is replaced. Name it `schemas`: consumers extract the tarball and mount the fixed
# path /tmp/schemas. Needs the release tags, because build_bundles.py --line builds the
# strict bundles of every earlier release in this compatibility line.
set -euo pipefail

mkdir -p "$1"
stage="$(cd "$1" && pwd)"
cd "$(dirname "$0")/.."
rm -rf "$stage" && mkdir -p "$stage"
# Everything a consumer needs, and nothing internal: .claude, .github, tests/ and the
# Jekyll Gemfiles are development tooling, not schema.
cp -r Core Operations Investments Dynamics TimeSeries docs scripts \
      openapi-*.json README.md CHANGELOG.md LICENSE "$stage/"
find "$stage" -name __pycache__ -prune -exec rm -rf {} +
# Strict validation bundles for every release in this line up to this one: a document
# stamped with any of them is checked against its own.
python3 scripts/build_bundles.py --line --out "$stage/bundles"
# The reader-rule test vectors, outside tests/ (which must not ship): each binding runs
# them against its own version check.
mkdir "$stage/versioning"
cp tests/fixtures/versioning/cases.json "$stage/versioning/"
