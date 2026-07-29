#!/usr/bin/env bash
set -euo pipefail

mode="${1:?mode is required: create or download}"
qdrant_url="${QDRANT_LOCAL_URL:-http://127.0.0.1:6333}"
qdrant_curl_max_time="${QDRANT_CURL_MAX_TIME:-120}"

if [[ -z "${QDRANT_API_KEY:-}" && -r /etc/qdrant/cluster.env ]]; then
  set -a
  # shellcheck source=/dev/null
  source /etc/qdrant/cluster.env
  set +a
fi

api_key="${QDRANT_API_KEY:-}"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'missing required command: %s\n' "$1" >&2
    exit 2
  }
}

curl_qdrant() {
  local method="$1"
  local path="$2"
  shift 2
  if [[ -n "$api_key" ]]; then
    curl --fail --silent --show-error --max-time "$qdrant_curl_max_time" \
      --request "$method" \
      --header "api-key: ${api_key}" \
      "$@" \
      "${qdrant_url}${path}"
  else
    curl --fail --silent --show-error --max-time "$qdrant_curl_max_time" \
      --request "$method" \
      "$@" \
      "${qdrant_url}${path}"
  fi
}

url_quote() {
  python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

require_command curl
require_command python3

case "$mode" in
  create)
    node_name="${2:?node name is required}"
    backup_id="${3:?backup id is required}"
    collections_json="$(curl_qdrant GET /collections)"
    collection_names="$(
      COLLECTIONS_JSON="$collections_json" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["COLLECTIONS_JSON"])
for item in payload.get("result", {}).get("collections", []):
    name = item.get("name")
    if name:
        print(name)
PY
    )"

    BACKUP_ID="$backup_id" NODE_NAME="$node_name" COLLECTION_NAMES="$collection_names" \
      QDRANT_API_KEY="$api_key" QDRANT_LOCAL_URL="$qdrant_url" python3 - <<'PY'
import json
import os
import urllib.parse
import urllib.request

api_key = os.environ.get("QDRANT_API_KEY", "")
base_url = os.environ.get("QDRANT_LOCAL_URL", "http://127.0.0.1:6333")
collections = [line for line in os.environ.get("COLLECTION_NAMES", "").splitlines() if line]

def request(method: str, path: str):
    req = urllib.request.Request(base_url + path, method=method)
    if api_key:
        req.add_header("api-key", api_key)
    with urllib.request.urlopen(req, timeout=300) as response:
        return json.loads(response.read().decode("utf-8"))

results = []
for collection in collections:
    encoded = urllib.parse.quote(collection, safe="")
    response = request("POST", f"/collections/{encoded}/snapshots?wait=true")
    result = response.get("result") or {}
    results.append(
        {
            "collection": collection,
            "snapshot_name": result.get("name"),
            "size": result.get("size"),
            "checksum": result.get("checksum"),
            "creation_time": result.get("creation_time"),
        }
    )

print(json.dumps({
    "node": os.environ["NODE_NAME"],
    "backup_id": os.environ["BACKUP_ID"],
    "collections": results,
}, sort_keys=True))
PY
    ;;
  download)
    collection="${2:?collection is required}"
    snapshot_name="${3:?snapshot name is required}"
    encoded_collection="$(url_quote "$collection")"
    encoded_snapshot="$(url_quote "$snapshot_name")"
    qdrant_curl_max_time="${QDRANT_DOWNLOAD_MAX_TIME:-840}"
    curl_qdrant GET "/collections/${encoded_collection}/snapshots/${encoded_snapshot}"
    ;;
  *)
    printf 'unsupported mode: %s\n' "$mode" >&2
    exit 2
    ;;
esac
