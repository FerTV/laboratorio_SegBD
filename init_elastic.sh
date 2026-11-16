#!/bin/sh
# Carga el índice "articulos" en Elasticsearch.
# Variables:
#   ES_URL                       (obligatoria)  p.ej. http://elasticsearch:9200
#   ES_AUTH                      (opcional)     usuario:contraseña del superusuario "elastic"
#   ES_APP_USER/ES_APP_PASSWORD  (opcional)     si se definen, crea un rol de solo
#                                               lectura sobre "articulos" y su usuario
set -eu

ES_URL="${ES_URL:?ES_URL no definida}"
# es(): curl con autenticación opcional. La cabecera Content-Type NO se fija
# aquí: cada llamada pone la suya (application/json o, en el _bulk,
# application/x-ndjson). Si se enviaran dos Content-Type, Elasticsearch rechaza
# el _bulk y el índice queda vacío.
if [ -n "${ES_AUTH:-}" ]; then
  es() { curl -sS -u "$ES_AUTH" "$@"; }
else
  es() { curl -sS "$@"; }
fi
JSON='Content-Type: application/json'

echo "[init_elastic] Esperando a Elasticsearch en $ES_URL ..."
until es "$ES_URL/_cluster/health" 2>/dev/null | grep -q '"status"'; do sleep 3; done

code=$(es -o /dev/null -w '%{http_code}' -I "$ES_URL/articulos" || true)
if [ "$code" = "200" ]; then
  echo "[init_elastic] El índice 'articulos' ya existe; no se recarga."
else
  echo "[init_elastic] Creando índice 'articulos'"
  es -XPUT "$ES_URL/articulos" -H "$JSON" -d '{
    "settings": { "number_of_shards": 1, "number_of_replicas": 0 },
    "mappings": { "properties": {
      "nombre":          { "type": "text" },
      "descripcion":     { "type": "text" },
      "categoria":       { "type": "keyword" },
      "precio":          { "type": "float" },
      "coste_proveedor": { "type": "float" },
      "proveedor":       { "type": "keyword" }
    }}
  }'
  echo
  # coste_proveedor y proveedor son datos internos/confidenciales: no deberían
  # llegar nunca al cliente de la API.
  cat > /tmp/bulk.ndjson <<'EOF'
{"index":{"_id":"1"}}
{"nombre":"Teclado mecánico","descripcion":"Teclado mecánico switches rojos","categoria":"perifericos","precio":89.9,"coste_proveedor":41.2,"proveedor":"KeyParts S.L."}
{"index":{"_id":"2"}}
{"nombre":"Ratón inalámbrico","descripcion":"Ratón ergonómico 2.4 GHz","categoria":"perifericos","precio":29.5,"coste_proveedor":11.0,"proveedor":"MouseCorp"}
{"index":{"_id":"3"}}
{"nombre":"Monitor 27","descripcion":"Monitor IPS 27 pulgadas 144 Hz","categoria":"pantallas","precio":259.0,"coste_proveedor":180.4,"proveedor":"PanelTech"}
{"index":{"_id":"4"}}
{"nombre":"Portátil 14","descripcion":"Portátil ligero 16 GB RAM","categoria":"equipos","precio":899.0,"coste_proveedor":702.0,"proveedor":"NoteBooks SA"}
{"index":{"_id":"5"}}
{"nombre":"Auriculares","descripcion":"Auriculares con cancelación de ruido","categoria":"audio","precio":149.0,"coste_proveedor":63.5,"proveedor":"SoundLab"}
{"index":{"_id":"6"}}
{"nombre":"Webcam HD","descripcion":"Webcam 1080p con micrófono","categoria":"perifericos","precio":49.0,"coste_proveedor":18.9,"proveedor":"MouseCorp"}
EOF
  resp=$(es -XPOST "$ES_URL/articulos/_bulk?refresh=true" \
            -H 'Content-Type: application/x-ndjson' --data-binary @/tmp/bulk.ndjson)
  if echo "$resp" | grep -q '"errors":true'; then
    echo "[init_elastic] ERROR en la carga bulk:"; echo "$resp"; exit 1
  fi
  n=$(es "$ES_URL/articulos/_count" | sed -n 's/.*"count":\([0-9]*\).*/\1/p')
  echo "[init_elastic] documentos cargados: ${n:-0}"
fi

if [ -n "${ES_APP_USER:-}" ] && [ -n "${ES_APP_PASSWORD:-}" ]; then
  echo "[init_elastic] Creando rol 'articulos_read' y usuario '$ES_APP_USER'"
  es -XPOST "$ES_URL/_security/role/articulos_read" -H "$JSON" -d '{
    "cluster": [],
    "indices": [ { "names": ["articulos"], "privileges": ["read"] } ]
  }'
  echo
  es -XPOST "$ES_URL/_security/user/$ES_APP_USER" -H "$JSON" -d "{
    \"password\": \"$ES_APP_PASSWORD\",
    \"roles\": [\"articulos_read\"],
    \"full_name\": \"Usuario de busqueda de la API\"
  }"
  echo
fi
echo "[init_elastic] Listo"
