#!/bin/bash
# WARNING: This file is auto-generated by snipinator. Do not edit directly.
# SOURCE: `rsynccheck/examples/hash-audit_example.sh.jinja2`.

# https://gist.github.com/mohanpedala/1e2ff5661761d3abd0385e8223e16425
set -e -x -v -u -o pipefail
set +v




YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Don't run this in act/GH actions because act doesn't play with with nested
# docker; the paths mess up.
if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
  echo -e "${YELLOW}This script is not meant to be run in GitHub Actions.${NC}"
  exit 0
fi
mkdir -p ".deleteme"

# Use a small chunk size because our files are small, and we want to demonstrate
# that the completion percentage is approximately correct.
CHUNK_SIZE=10
SRC_DIRECTORY=./rsynccheck/examples
DST_DIRECTORY=.deleteme/destination

rm -Rf "${DST_DIRECTORY}"
mkdir -p "${DST_DIRECTORY}"

set +x +v
find "${SRC_DIRECTORY}" -type f -name "*" -print0 | while IFS= read -r -d '' PWD_REL_PATH; do
  ABS_PATH=$(realpath "${PWD_REL_PATH}")
  SRC_REL_PATH="${PWD_REL_PATH#${SRC_DIRECTORY}/}"
  SIZE=$(stat --printf="%s" "${ABS_PATH}")
  DST_PATH="${DST_DIRECTORY}/${SRC_REL_PATH}"
  mkdir -p "$(dirname ${DST_PATH})"
  # Copy half the file.
  dd if="${ABS_PATH}" of="${DST_PATH}" bs=1 count=$((SIZE/2)) > /dev/null 2>&1
done
set -x -v

# INCORRECT_SNIPPET_START
# Use the published images at ghcr.io/realazthat/rsynccheck.
# Generate the audit.yaml file.
# /data in the docker image is the working directory, so paths are simpler.
docker run --rm --tty \
  -v "${PWD}:/data" \
  ghcr.io/realazthat/rsynccheck:v0.0.1 \
  hash \
  --ignorefile ".gitignore" \
  --ignoreline .trunk --ignoreline .git \
  --audit-file ".deleteme/check-changes-audit.yaml" \
  --progress none \
  --chunk-size "${CHUNK_SIZE}" \
  --directory "${SRC_DIRECTORY}"

# Check the audit.yaml file on the other machine.
docker run --rm --tty \
  -v "${PWD}:/data" \
  ghcr.io/realazthat/rsynccheck:v0.0.1 \
  audit \
  --audit-file ".deleteme/check-changes-audit.yaml" \
  --progress none \
  --output-format table \
  --mismatch-exit 0 \
  --directory "${DST_DIRECTORY}"
# INCORRECT_SNIPPET_END

# Now copy all the files correctly.
rm -Rf "${DST_DIRECTORY}"
rsync -a "${SRC_DIRECTORY}/" "${DST_DIRECTORY}"

# CORRECT_SNIPPET_START
docker run --rm --tty \
  -v "${PWD}:/data" \
  ghcr.io/realazthat/rsynccheck:v0.0.1 \
  audit \
  --audit-file ".deleteme/check-changes-audit.yaml" \
  --progress none \
  --output-format table \
  --mismatch-exit 1 \
  --directory "${DST_DIRECTORY}"
# CORRECT_SNIPPET_END
