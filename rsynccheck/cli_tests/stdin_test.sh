#!/bin/bash
# https://gist.github.com/mohanpedala/1e2ff5661761d3abd0385e8223e16425
set -e -x -v -u -o pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'


TMP_DIR=$(mktemp -d)
ORIGINAL_PWD="${PWD}"

function delete_tmp_dir {
  cd "${ORIGINAL_PWD}"
  rm -rf "${TMP_DIR}"
}
trap delete_tmp_dir EXIT


################################################################################
mkdir -p "${TMP_DIR}/original"
mkdir -p "${TMP_DIR}/copy"

################################################################################
dd if=/dev/urandom of="${TMP_DIR}/original/file1" bs=1M count=1
################################################################################
rsync -a "${TMP_DIR}/original/" "${TMP_DIR}/copy/"
################################################################################
python -m rsynccheck.cli hash \
  --directory "${TMP_DIR}/original" > "${TMP_DIR}/audit.stdout.yaml" \
  --audit-file '-'

python -m rsynccheck.cli hash \
  --directory "${TMP_DIR}/original" \
  --audit-file "${TMP_DIR}/audit.yaml"

# They should be the same.
git diff --no-index --exit-code \
  "${TMP_DIR}/audit.stdout.yaml" "${TMP_DIR}/audit.yaml"
echo -e "${GREEN}stdout matches expected output${NC}"
################################################################################
# Audit directory: should pass.
python -m rsynccheck.cli audit \
  --directory "${TMP_DIR}/copy" \
  --audit-file "${TMP_DIR}/audit.yaml"

# Now audit from stdin.
cat "${TMP_DIR}/audit.yaml" \
  | python -m rsynccheck.cli audit \
    --directory "${TMP_DIR}/copy" \
    --audit-file "-"
echo -e "${GREEN}stdin matches expected output${NC}"
################################################################################
# Alter directory and reaudit; both should fail.
dd if=/dev/urandom of="${TMP_DIR}/copy/file1" bs=1M count=1

# Now audit: expected to fail.
EXIT_CODE=0
unbuffer python -m rsynccheck.cli audit \
  --directory "${TMP_DIR}/copy" \
  --audit-file "${TMP_DIR}/audit.yaml" \
  --mismatch-exit 10 \
  > "${TMP_DIR}/error.log" 2>&1 \
  || EXIT_CODE=$?

if [[ "${EXIT_CODE}" -ne 10 ]]; then
  cat "${TMP_DIR}/error.log"
  echo -e "${RED}Expected rsynccheck to fail with exit code 10 when the file is edited${NC}"
  exit 1
fi

# Now audit from stdin.
cat "${TMP_DIR}/audit.yaml" \
  | python -m rsynccheck.cli audit \
    --directory "${TMP_DIR}/copy" \
    --audit-file "-" \
  >/dev/null 2>&1 \
  || EXIT_CODE=$?

if [[ "${EXIT_CODE}" -eq 0 ]]; then
  echo -e "${RED}Expected rsynccheck to fail when the file is edited${NC}"
  exit 1
fi


echo -e "${GREEN}Successfully generated expected output${NC}"



echo -e "${GREEN}${BASH_SOURCE[0]}: All tests passed${NC}"
