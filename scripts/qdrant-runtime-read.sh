#!/usr/bin/env bash
set -euo pipefail

node_name="${1:?node name is required}"
if [[ -z "${QDRANT_API_KEY:-}" && -r /etc/qdrant/cluster.env ]]; then
  set -a
  # shellcheck source=/dev/null
  source /etc/qdrant/cluster.env
  set +a
fi

api_key="${QDRANT_API_KEY:-}"
qdrant_url="${QDRANT_LOCAL_URL:-http://127.0.0.1:6333}"
storage_path="${QDRANT_STORAGE_PATH:-/qdrant/storage}"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'missing required command: %s\n' "$1" >&2
    exit 2
  }
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

curl_json() {
  local path="$1"
  local output
  if [[ -n "$api_key" ]]; then
    output="$(curl --fail --silent --show-error --max-time 10 \
      --header "api-key: ${api_key}" \
      "${qdrant_url}${path}" 2>/dev/null || true)"
  else
    output="$(curl --fail --silent --show-error --max-time 10 \
      "${qdrant_url}${path}" 2>/dev/null || true)"
  fi
  if [[ -z "$output" ]]; then
    printf 'null'
  else
    printf '%s' "$output"
  fi
}

docker_inspect_json() {
  local container="$1"
  docker inspect "$container" 2>/dev/null || printf '[]'
}

require_command curl
require_command docker
require_command python3

collections_json="$(curl_json /collections)"
collection_names="$(
  COLLECTIONS_JSON="$collections_json" python3 - <<'PY'
import json
import os

try:
    payload = json.loads(os.environ["COLLECTIONS_JSON"])
except Exception:
    payload = {}

collections = payload.get("result", {}).get("collections", [])
for item in collections:
    name = item.get("name")
    if name:
        print(name)
PY
)"

collection_reports="[]"
if [[ -n "$collection_names" ]]; then
  collection_reports="$(
    COLLECTION_NAMES="$collection_names" \
      QDRANT_API_KEY="$api_key" \
      QDRANT_LOCAL_URL="$qdrant_url" \
      python3 - <<'PY'
import json
import os
import subprocess
import urllib.parse
import urllib.request

api_key = os.environ.get("QDRANT_API_KEY", "")
base_url = os.environ.get("QDRANT_LOCAL_URL", "http://127.0.0.1:6333")
names = [line for line in os.environ.get("COLLECTION_NAMES", "").splitlines() if line]

def get(path: str):
    request = urllib.request.Request(base_url + path)
    if api_key:
        request.add_header("api-key", api_key)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None

reports = []
for name in names:
    encoded = urllib.parse.quote(name, safe="")
    info = get(f"/collections/{encoded}")
    snapshots = get(f"/collections/{encoded}/snapshots")
    params = (((info or {}).get("result") or {}).get("config") or {}).get("params") or {}
    reports.append(
        {
            "name": name,
            "status": ((info or {}).get("result") or {}).get("status"),
            "replication_factor": params.get("replication_factor"),
            "write_consistency_factor": params.get("write_consistency_factor"),
            "points_count": ((info or {}).get("result") or {}).get("points_count"),
            "snapshot_count": len(((snapshots or {}).get("result") or [])),
        }
    )

print(json.dumps(reports, sort_keys=True))
PY
  )"
fi

storage_stat="{}"
if [[ -e "$storage_path" ]]; then
  storage_stat="$(
    STORAGE_PATH="$storage_path" python3 - <<'PY'
import json
import os
import shutil

path = os.environ["STORAGE_PATH"]
usage = shutil.disk_usage(path)
print(json.dumps({
    "path": path,
    "total_bytes": usage.total,
    "used_bytes": usage.used,
    "free_bytes": usage.free,
}))
PY
  )"
fi

cat <<JSON
{
  "node": $(printf '%s' "$node_name" | json_escape),
  "hostname": $(hostname | json_escape),
  "qdrant_container": $(docker_inspect_json qdrant_node${node_name##*-}),
  "metrics_proxy_container": $(docker_inspect_json qdrant_metrics_proxy),
  "readiness": $(curl_json /readyz),
  "cluster": $(curl_json /cluster),
  "collections": ${collection_reports},
  "storage": ${storage_stat}
}
JSON
