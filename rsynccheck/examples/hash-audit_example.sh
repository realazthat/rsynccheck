#!/bin/bash
# WARNING: This file is auto-generated by snipinator. Do not edit directly.
# SOURCE: `rsynccheck/examples/hash-audit_example.sh.jinja2`.

# https://gist.github.com/mohanpedala/1e2ff5661761d3abd0385e8223e16425
set -e -x -v -u -o pipefail
set +v



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
# Generate the audit.yaml file.
python -m rsynccheck.cli \
  hash \
  --ignorefile ".gitignore" \
  --ignoreline .trunk --ignoreline .git \
  --audit-file ".deleteme/check-changes-audit.yaml" \
  --progress none \
  --chunk-size "${CHUNK_SIZE}" \
  --directory "${SRC_DIRECTORY}"

# Check the audit.yaml file on the other machine.
python -m rsynccheck.cli \
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
python -m rsynccheck.cli \
  audit \
  --audit-file ".deleteme/check-changes-audit.yaml" \
  --progress none \
  --output-format table \
  --mismatch-exit 1 \
  --directory "${DST_DIRECTORY}"
# CORRENT_SNIPPET_END
