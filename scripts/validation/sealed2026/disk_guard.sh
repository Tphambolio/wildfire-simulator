#!/usr/bin/env bash
# Disk guard for the sealed-confirm runs (added after the 2026-10-25 ENOSPC interruption; changes no result).
# Every 30 s: when / has < MIN_GB free, SIGSTOP every validate.py process of this run (pattern below);
# SIGCONT them once >= MIN_GB is free again. Exits when no matching process is left.
MIN_GB=${MIN_GB:-10}
PAT=${PAT:-sealed2026-confirm/domains}
stopped=0
while pgrep -f "$PAT" >/dev/null; do
  free_gb=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
  if [ "$free_gb" -lt "$MIN_GB" ] && [ $stopped = 0 ]; then
    pkill -STOP -f "$PAT"; stopped=1; echo "$(date -Is) paused: ${free_gb} G free"
  elif [ "$free_gb" -ge "$MIN_GB" ] && [ $stopped = 1 ]; then
    pkill -CONT -f "$PAT"; stopped=0; echo "$(date -Is) resumed: ${free_gb} G free"
  fi
  sleep 30
done
echo "$(date -Is) disk guard exit"
