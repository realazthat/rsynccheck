#!/bin/bash
# https://gist.github.com/mohanpedala/1e2ff5661761d3abd0385e8223e16425
set -e -x -v -u -o pipefail

SCRIPT_DIR=$(realpath "$(dirname "${BASH_SOURCE[0]}")")
source "${SCRIPT_DIR}/utilities/common.sh"

# NOTE: Use dev requirements to generate the README because the README uses
# shell() with some tools that we only want to install into dev environment.
VENV_PATH="${PWD}/.cache/scripts/.venv" source "${PROJ_PATH}/scripts/utilities/ensure-venv.sh"
TOML=${PROJ_PATH}/pyproject.toml EXTRA=dev \
  DEV_VENV_PATH="${PWD}/.cache/scripts/.venv" \
  TARGET_VENV_PATH="${PWD}/.cache/scripts/.venv" \
  bash "${PROJ_PATH}/scripts/utilities/ensure-reqs.sh"

# Avoid the terminal-width==0 warning, because that happens in GH actions,
# and makes the example output inconsistent.
export SUPPRESS_TERMINAL_WARNING=1



# This happens in generate.sh.
# bash scripts/run-all-examples.sh

mkdir -p .deleteme
# Try to make terminal output as consistent as possible.
TERM=xterm-256color COLUMNS=160 LINES=40 \
unbuffer bash ./rsynccheck/examples/hash-audit_example.sh \
  > .deleteme/hash-audit_example.output 2>&1


python -m snipinator.cli \
  -t "${PROJ_PATH}/README.md.jinja2" \
  --rm \
  --force \
  --create \
  -o "${PROJ_PATH}/README.md" \
  --chmod-ro

LAST_VERSION=$(tomlq -r -e '.["tool"]["rsynccheck-project-metadata"]["last_stable_release"]' pyproject.toml)
python -m mdremotifier.cli \
  -i "${PROJ_PATH}/README.md" \
  --url-prefix "https://github.com/realazthat/rsynccheck/blob/v${LAST_VERSION}/" \
  --img-url-prefix "https://raw.githubusercontent.com/realazthat/rsynccheck/v${LAST_VERSION}/" \
  -o "${PROJ_PATH}/.github/README.remotified.md"
