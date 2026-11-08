#!/bin/bash
# Rulează secvențial toate variantele SSSM pe T3. Fiecare rulare: proces python
# izolat (o cădere nu le oprește pe celelalte), timeout 1h/rulare.
cd /srv/robo-zero
PY=/srv/robo-zero/.venv/bin/python
LOG=/srv/robo-zero/scratchpad/sssm/run_all.log
echo "=== START $(date -Is) ===" >> "$LOG"
for spec in A:1 A:2 B:1 B:2 AB:1 AB:2 V1:1 V1:2; do
  V="${spec%%:*}"; R="${spec##*:}"
  echo "--- $V run $R START $(date -Is) ---" >> "$LOG"
  timeout 3600 "$PY" scratchpad/sssm/harness.py "$V" "$R" >> "$LOG" 2>&1
  echo "--- $V run $R END rc=$? $(date -Is) ---" >> "$LOG"
done
echo "=== DONE $(date -Is) ===" >> "$LOG"
