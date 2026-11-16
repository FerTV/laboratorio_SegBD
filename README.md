# Laboratorio · Seguridad en NoSQL (MongoDB + Elasticsearch) con FastAPI

Repositorio de la actividad. Contiene un entorno **baseline inseguro** y un entorno
**endurecido**, una API FastAPI en sus dos versiones (vulnerable y segura) y los scripts
de datos, copias de seguridad y alertas.

## Requisitos

- Docker Engine / Docker Desktop con Docker Compose v2
- `curl`, `jq`, `nmap` y `mongosh` en el host

## Estructura

```
.
├── docker-compose.yml          # entorno BASELINE inseguro (Mongo sin --auth, ES sin seguridad)
├── docker-compose.secure.yml   # entorno ENDURECIDO (auth/RBAC, red interna, TLS)
├── Caddyfile                   # proxy inverso TLS (único punto de entrada del entorno seguro)
├── .env.example                # credenciales del entorno seguro (copiar a .env)
├── init_mongo.js               # datos del baseline (contraseñas en claro)
├── init_mongo_secure.js        # usuario de app readWrite + contraseñas PBKDF2
├── init_elastic.sh             # índice "articulos" (+ rol/usuario de solo lectura)
├── fastapi-app/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main_vulnerable.py      # API vulnerable (NoSQLi, consulta sin filtrar a ES)
│   └── main_secure.py          # API endurecida (Pydantic, rate-limit, logs JSON)
└── scripts/
    ├── backup.sh               # mongodump + snapshot de Elasticsearch
    └── alertas.sh              # alertas por logins fallidos / picos de búsqueda
```

Las dos versiones de la API están en el mismo contenedor; la variable de entorno
`APP_MODULE` selecciona cuál se ejecuta (`main_vulnerable:app` en el baseline,
`main_secure:app` en el seguro).

## Credenciales de prueba de la aplicación

| Usuario | Contraseña   | Rol     |
|---------|--------------|---------|
| admin   | `Adm1n!2026` | admin   |
| alice   | `alice123`   | cliente |
| bob     | `bob123`     | cliente |

En el baseline se guardan en claro; en el entorno seguro como PBKDF2-SHA256.

---

## Paso 1 · Arranque del baseline inseguro

```bash
docker compose -f docker-compose.yml up -d --build
docker compose -f docker-compose.yml ps      # espera a que todo esté "Up"/"healthy"
```

**Smoke test y CRUD** (la API responde en `http://localhost:8000`):

```bash
# Smoke test
curl -s localhost:8000/pedidos | jq .
curl -s "localhost:8000/articulos?q=teclado" | jq .

# CRUD de un pedido
ID=$(curl -s -X POST localhost:8000/pedidos -H 'Content-Type: application/json' \
      -d '{"producto":"Alfombrilla","cantidad":3}' | jq -r ._id)
curl -s localhost:8000/pedidos/$ID | jq .
curl -s -X PUT localhost:8000/pedidos/$ID -H 'Content-Type: application/json' \
      -d '{"producto":"Alfombrilla XL","cantidad":5}' | jq .
curl -s -X DELETE localhost:8000/pedidos/$ID | jq .
```

### Cargar el índice `articulos` (si no existe)

El índice de Elasticsearch lo crea y rellena el servicio `es-init` al arrancar. Si
`GET /articulos` devuelve `index_not_found_exception`, o si ya ejecutaste el ataque de
borrado del Paso 3, vuelve a cargarlo lanzando ese servicio de nuevo:

```bash
docker compose -f docker-compose.yml run --rm es-init
curl -s 'localhost:9200/articulos/_count'      # debe devolver "count":6
```

`es-init` no recrea el índice si ya existe (solo lo carga cuando falta), así que es seguro
ejecutarlo las veces que haga falta. En el entorno endurecido se usa el mismo comando con
`-f docker-compose.secure.yml`.

## Paso 2 · Reconocimiento

```bash
# Puertos expuestos en el host
nmap -Pn -sT -p 8000,8443,9200,27017 localhost

# Elasticsearch sin autenticación ni seguridad
curl -s localhost:9200 | jq .
curl -s 'localhost:9200/_xpack?filter_path=features.security' | jq .

# MongoDB sin --auth ni usuarios
mongosh --quiet mongodb://localhost:27017/tienda \
  --eval 'printjson({argv: db.adminCommand({getCmdLineOpts:1}).argv, usuarios: db.getUsers().users})'

# Logging por defecto de la API
docker compose -f docker-compose.yml logs --tail 15 fastapi-app
```

## Paso 3 · Ataques controlados

```bash
# NoSQL Injection en el login (bypass con $ne)
curl -s -X POST localhost:8000/login_mongo -H 'Content-Type: application/json' \
      -d '{"username":"alice","password":{"$ne":"x"}}' | jq .

# Lectura y escritura anónima en MongoDB (sin usuario ni contraseña)
mongosh --quiet mongodb://localhost:27017/tienda \
  --eval 'printjson(db.usuarios.find({}, {_id:0}).toArray())'
mongosh --quiet mongodb://localhost:27017/tienda \
  --eval 'printjson(db.pedidos.insertOne({producto:"PEDIDO_ANONIMO", cantidad:999, cliente:"atacante"}))'
mongosh --quiet mongodb://localhost:27017/tienda \
  --eval 'printjson(db.pedidos.deleteMany({producto:"PEDIDO_ANONIMO"}))'   # limpia lo que insertó el ataque

# Elasticsearch abierto
curl -s 'localhost:9200/_cat/indices?v'
curl -s 'localhost:9200/articulos/_search?q=*' | jq '.hits.hits[]._source'
curl -s -XDELETE localhost:9200/articulos | jq .      # ¡solo dataset de prueba!
```

> Este ataque borra el índice. Para volver a tener datos, recárgalo con
> `docker compose -f docker-compose.yml run --rm es-init` (ver
> [Cargar el índice articulos](#cargar-el-índice-articulos-si-no-existe)).

Para dejarlo limpio antes de pasar al entorno seguro:

```bash
docker compose -f docker-compose.yml down -v          # -v borra los datos de prueba
```

---

## Paso 4 · Entorno endurecido

```bash
cp .env.example .env        # y CAMBIA todas las contraseñas (sin comillas dobles)
# Si ya habías levantado el entorno seguro antes, los volúmenes conservan las credenciales
# antiguas (los scripts de init solo se ejecutan con el volumen vacío). Bórralos primero:
docker compose -f docker-compose.secure.yml down -v
docker compose -f docker-compose.secure.yml up -d --build
docker compose -f docker-compose.secure.yml ps
```

Las bases de datos ya no publican puerto; el único acceso es el proxy TLS en
`https://localhost:8443` (certificado de una CA interna de Caddy, por eso `curl -k`):

```bash
curl -sk https://localhost:8443/health        # {"status":"ok"}
```

## Paso 5 · Verificación (los ataques deben fallar)

```bash
# Puertos de BD ya no accesibles (solo 8443)
nmap -Pn -sT -p 8000,8443,9200,27017 localhost

# NoSQLi bloqueada -> 422
curl -sk -o /dev/null -w '%{http_code}\n' -X POST https://localhost:8443/login_mongo \
      -H 'Content-Type: application/json' -d '{"username":"alice","password":{"$ne":"x"}}'

# Fuerza bruta -> 429 a partir del 6.º intento
for i in $(seq 1 7); do
  curl -sk -o /dev/null -w "intento $i -> %{http_code}\n" -X POST https://localhost:8443/login_mongo \
    -H 'Content-Type: application/json' -d '{"username":"bob","password":"mala"}'
done

# MongoDB exige autenticación (incluso desde la red interna): lectura y escritura anónimas -> "requires authentication"
docker compose -f docker-compose.secure.yml exec -T mongo \
  mongosh --quiet tienda --eval 'db.usuarios.find().toArray()'
docker compose -f docker-compose.secure.yml exec -T mongo \
  mongosh --quiet tienda --eval 'db.pedidos.insertOne({producto:"PEDIDO_ANONIMO", cantidad:999})'

# Elasticsearch: acceso anónimo (401) y RBAC del usuario de app (403 al borrar)
source .env
docker compose -f docker-compose.secure.yml exec -T elasticsearch \
  curl -s -o /dev/null -w '%{http_code}\n' 'localhost:9200/_cat/indices?v'
docker compose -f docker-compose.secure.yml exec -T elasticsearch \
  curl -s -o /dev/null -w '%{http_code}\n' -u "$ES_APP_USER:$ES_APP_PASSWORD" -XDELETE 'localhost:9200/articulos'

# Búsqueda controlada: solo campos públicos; Lucene y paginación abusiva -> 422
curl -sk 'https://localhost:8443/articulos?q=teclado' | jq .
curl -sk -o /dev/null -w '%{http_code}\n' 'https://localhost:8443/articulos?q=coste_proveedor:>100'

# Trazabilidad: eventos de seguridad en los logs
docker compose -f docker-compose.secure.yml logs fastapi-app | grep '"event"'
```

## Copias de seguridad y alertas

```bash
./scripts/backup.sh        # mongodump (comprimido + SHA-256) y snapshot de Elasticsearch -> backups/
./scripts/alertas.sh       # resume logins fallidos, bloqueos y picos de búsqueda de los logs
```

## Parada y limpieza

```bash
docker compose -f docker-compose.secure.yml down -v
```
