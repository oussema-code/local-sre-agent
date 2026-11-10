#!/usr/bin/env bash
# Fault injection for the demo: service-down | disk-full | disk-full-hard | bad-config | reset | status
set -euo pipefail
cd "$(dirname "$0")/.."
APP=edge-app
CONFIG=config/app.yaml
PROM=http://localhost:9090
URL=http://localhost:${EDGE_PORT:-18080}

disk_pct() { docker exec "$APP" df --output=pcent /data | tail -1 | tr -dc 0-9; }

# Fill $1 (a dir inside /data) with 4 MB files until the volume is >= 90 % used.
fill() {
  docker exec "$APP" sh -c "mkdir -p $1; i=0; while [ \$(df --output=pcent /data | tail -1 | tr -dc 0-9) -lt 90 ]; do
    dd if=/dev/zero of=$1/dump-\$(date +%s)-\$i.log bs=1M count=4 2>/dev/null; i=\$((i+1)); done"
  echo "disk usage: $(disk_pct)%"
}

prom() { curl -s "$PROM/api/v1/query" --data-urlencode "query=$1" |
  python3 -c 'import json,sys; r=json.load(sys.stdin)["data"]["result"]; print(r[0]["value"][1] if r else "n/a")'; }

case "${1:-}" in
  service-down)   docker stop "$APP" ;;
  disk-full)      fill /data/logs ;;                  # debug dumps left in the log dir: cleanup can fix it
  disk-full-hard) fill /data/cache ;;                 # filler outside logs/: cleanup cannot fix it -> undo + escalate
  bad-config)
    sed -i 's/^latency_ms:.*/latency_ms: 1500/' "$CONFIG"
    echo "pushed bad config: latency_ms=1500" ;;
  reset)
    docker start "$APP" >/dev/null
    docker exec "$APP" sh -c 'rm -rf /data/cache /data/logs/dump-*'
    sed -i 's/^latency_ms:.*/latency_ms: 0/' "$CONFIG"
    echo "reset done" ;;
  status)
    echo "container : $(docker inspect -f '{{.State.Status}}' "$APP")"
    echo "health    : $(curl -s -o /dev/null -w '%{http_code}' --max-time 2 $URL/health || true)"
    echo "latency   : $(curl -s -o /dev/null -w '%{time_total}s' --max-time 5 $URL/api/status || echo timeout)"
    echo "disk      : $(disk_pct 2>/dev/null || echo n/a)%"
    echo "config    : $(grep latency_ms "$CONFIG")"
    echo "prom up   : $(prom 'up{job="edge-app"}')"
    echo "prom p95  : $(prom 'histogram_quantile(0.95, sum by (le)(rate(edge_request_duration_seconds_bucket{path="/api/status"}[30s])))')"
    echo "prom disk : $(prom 'edge_disk_used_ratio')" ;;
  *) echo "usage: $0 service-down|disk-full|disk-full-hard|bad-config|reset|status" >&2; exit 1 ;;
esac
