#!/usr/bin/env bash
# The one check command for this repo: CI and coding agents both run it.
# A change is done when this exits 0.
#
# CI-only, not run here: the VS Code Extension Host acceptance test
# (`xvfb-run -a npm run test:host` in vscode-extension/), which needs an X
# server, downloads VS Code and talks to a live daemon. See the
# vscode-host job in .github/workflows/ci.yml.
set -euo pipefail
cd "$(dirname "$0")/.."

uv sync --locked
# Coverage gate (85% on muvue.core) comes from [tool.coverage.report].
uv run pytest -q --cov=muvue.core --cov-report=term
uv run pytest -q -s tests/test_hook_latency_benchmark.py

(cd dashboard && npm ci && npm run typecheck && npm test && npm run build)
# The committed page must be the build of this source.
git diff --exit-code -- src/muvue/api/static/index.html

(cd vscode-extension && npm ci && npm test)
