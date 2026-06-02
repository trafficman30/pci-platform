#!/usr/bin/env bash
# /opt/pci/tools/rotate_logs.sh
# Gzip previous day's MOVA JSONL logs and delete files older than 30 days.
# Runs daily at 00:05 via pci-rotate-logs.timer.
#
# Log directory: first argument, else $MOVA_LOG_DIR, else /opt/pci/logs/mova

set -euo pipefail

LOG_DIR="${1:-${MOVA_LOG_DIR:-/opt/pci/logs/mova}}"
TODAY="$(date +%Y-%m-%d)"

if [ ! -d "$LOG_DIR" ]; then
    echo "rotate_logs: log directory not found: $LOG_DIR — nothing to do" >&2
    exit 0
fi

# Compress previous days — stream_N_YYYY-MM-DD.jsonl, skipping today's live file
find "$LOG_DIR" -maxdepth 1 -name 'stream_*.jsonl' ! -name "*${TODAY}*" \
    -exec gzip --best {} \;

# Prune compressed files older than 30 days
find "$LOG_DIR" -maxdepth 1 -name 'stream_*.jsonl.gz' -mtime +30 -delete

echo "rotate_logs: done  dir=$LOG_DIR  date=$TODAY"
