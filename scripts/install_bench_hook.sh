#!/usr/bin/env bash
# G2: instalează .git/hooks/post-merge care rulează bench/after_merge.sh
# (doar pe branch-ul main). Idempotent; un hook existent și diferit e salvat
# ca post-merge.bak-<ts>. Nu se execută el însuși la instalare.
set -euo pipefail

HOOKS="$(git rev-parse --git-path hooks)"
mkdir -p "${HOOKS}"
TARGET="${HOOKS}/post-merge"

BODY='#!/bin/sh
# G2: benchmark de regresie după merge pe main (scripts/install_bench_hook.sh)
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || exit 0
nohup "$(git rev-parse --show-toplevel)/bench/after_merge.sh" "$(date +%s)" >/dev/null 2>&1 &
exit 0'

if [ -f "${TARGET}" ] && [ "$(cat "${TARGET}")" != "${BODY}" ]; then
  cp "${TARGET}" "${TARGET}.bak-$(date +%s)"
fi

printf '%s\n' "${BODY}" >"${TARGET}"
chmod +x "${TARGET}"
echo "post-merge instalat: ${TARGET}"
