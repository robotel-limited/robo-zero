#!/usr/bin/env bash
# G2: benchmark de regresie rulat după fiecare merge pe main.
# Instalat de scripts/install_bench_hook.sh ca .git/hooks/post-merge.
# Arg 1 = timestamp unix al merge-ului. Nu repornește/oprește servicii.
set -u

MERGE_TS="${1:-}"
case "${MERGE_TS}" in
  ''|*[!0-9]*)
    echo "usage: after_merge.sh <unix_ts>" >&2
    exit 0
    ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 0

mkdir -p bench/results reports

exec 9>bench/results/.lock
flock -n 9 || exit 0          # altă rulare în curs

SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
STAMP="$(date +%Y-%m-%d_%H%M)"
REPORT="reports/bench_${STAMP}_${SHA}.txt"
LABEL="merge-${SHA}-${MERGE_TS}"
NEW_JSONL="bench/results/${LABEL}.jsonl"

# Așteaptă repornirea serviciului după merge (poll 10s, max 15 min).
ready=0
deadline=$(( $(date +%s) + 900 ))
while [ "$(date +%s)" -lt "${deadline}" ]; do
  svc_raw="$(systemctl show -p ActiveEnterTimestamp --value robo-zero.service 2>/dev/null || true)"
  svc_ts=0
  if [ -n "${svc_raw}" ]; then
    svc_ts="$(date -d "${svc_raw}" +%s 2>/dev/null || echo 0)"
  fi
  if [ "${svc_ts:-0}" -gt "${MERGE_TS}" ] \
     && curl -fs http://127.0.0.1:8150/ -o /dev/null 2>/dev/null; then
    ready=1
    break
  fi
  sleep 10
done

if [ "${ready}" != "1" ]; then
  {
    echo "== bench după merge: sha=${SHA} merge_ts=${MERGE_TS} $(date -Is) =="
    echo "serviciul nu a fost repornit după merge, benchmark sărit"
  } >"${REPORT}" 2>&1
  cp "${REPORT}" bench/results/latest-report.txt
  exit 0
fi

{
  echo "== bench după merge: sha=${SHA} merge_ts=${MERGE_TS} $(date -Is) =="
  echo
  echo "\$ python3 bench/run_bench.py ${LABEL}"
  python3 bench/run_bench.py "${LABEL}"
  echo
  echo "\$ python3 bench/compare.py ${NEW_JSONL}"
  python3 bench/compare.py "${NEW_JSONL}"
} >"${REPORT}" 2>&1

cp "${REPORT}" bench/results/latest-report.txt
exit 0
