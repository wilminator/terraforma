#!/bin/env bash
# Runs the full unit battery in Docker and keeps a log of the run in test-results/ (unittest-<timestamp>.log), so a
# person or a Claude thread can read the result without it being relayed. The last line is the verdict:
#   RESULT: PASS|FAIL (exit code N), commit <sha>, <seconds>s
# After a passing run only the newest 10 logs are kept; a failing run deletes nothing.
cd "$(dirname "$0")/.." || exit 1
set -o pipefail
mkdir -p test-results
log="test-results/unittest-$(date +%Y%m%d-%H%M%S).log"
started=$SECONDS
docker compose run --remove-orphans --rm test -n auto "$@" 2>&1 | tee "$log"
code=$?
if [ "$code" -eq 0 ]; then verdict=PASS; else verdict=FAIL; fi
echo "RESULT: $verdict (exit code $code), commit $(git rev-parse --short HEAD 2>/dev/null || echo unknown), $((SECONDS - started))s" | tee -a "$log"
if [ "$code" -eq 0 ]; then
    ls -1t test-results/unittest-*.log | tail -n +11 | xargs -r rm -f --
fi
exit "$code"
