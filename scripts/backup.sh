#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
DC="docker compose -f docker-compose.secure.yml"
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p backups

echo "[backup] mongodump de 'tienda'"
$DC exec -T mongo sh -c 'mongodump --quiet -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin --db tienda --archive --gzip' > "backups/mongo_tienda_$TS.archive.gz"
sha256sum "backups/mongo_tienda_$TS.archive.gz" > "backups/mongo_tienda_$TS.archive.gz.sha256"

echo "[backup] snapshot de Elasticsearch"
$DC exec -T elasticsearch sh -s <<EOF
curl -s -u "elastic:\$ELASTIC_PASSWORD" -X PUT localhost:9200/_snapshot/lab_repo \
  -H 'Content-Type: application/json' \
  -d '{"type":"fs","settings":{"location":"/usr/share/elasticsearch/data/snapshots"}}'
echo
curl -s -u "elastic:\$ELASTIC_PASSWORD" -X PUT \
  "localhost:9200/_snapshot/lab_repo/snap_$TS?wait_for_completion=true&filter_path=snapshot.snapshot,snapshot.state,snapshot.indices"
echo
EOF

echo "[backup] ficheros locales:"
ls -lh backups/ | tail -n 4
