"""API NoSQL ENDURECIDA.

Contramedidas:
  * Validación estricta con Pydantic (StrictStr, patrones, extra="forbid"):
    cualquier objeto/operador "$" en la entrada -> 422.
  * Login por username + verificación PBKDF2 en la aplicación (la contraseña
    nunca forma parte del filtro de MongoDB). Comparación en tiempo constante.
  * Rate-limit de intentos fallidos de login (-> 429).
  * Conexión a MongoDB con usuario de aplicación (rol readWrite sobre "tienda").
  * Búsqueda en ES con multi_match (sin sintaxis Lucene), lista blanca de
    caracteres, paginación acotada y _source filtrado (sin campos internos).
  * Conexión a ES con usuario de solo lectura sobre "articulos".
  * Logging de seguridad en JSON (stdout) y alertas por picos de búsqueda.
"""
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import requests
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from pymongo import MongoClient

# ---------------------------------------------------------------- logging
class JsonFormatter(logging.Formatter):
    def format(self, record):
        base = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
        }
        base.update(getattr(record, "extra_fields", {}))
        if "event" not in base:
            base["msg"] = record.getMessage()
        return json.dumps(base, ensure_ascii=False)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(JsonFormatter())
seclog = logging.getLogger("security")
seclog.setLevel(logging.INFO)
seclog.addHandler(_handler)
seclog.propagate = False


def sec_event(event: str, level=logging.INFO, **fields):
    seclog.log(level, event, extra={"extra_fields": {"event": event, **fields}})


# ---------------------------------------------------------------- config
MONGO = MongoClient(
    os.getenv("MONGO_HOST", "mongo"),
    27017,
    username=os.environ["MONGO_APP_USER"],
    password=os.environ["MONGO_APP_PASSWORD"],
    authSource=os.getenv("MONGO_DATABASE", "tienda"),
    serverSelectionTimeoutMS=5000,
)
db = MONGO[os.getenv("MONGO_DATABASE", "tienda")]
ES = f"http://{os.getenv('ELASTIC_HOST', 'elasticsearch')}:9200"
ES_AUTH = (os.environ["ES_APP_USER"], os.environ["ES_APP_PASSWORD"])

LOGIN_MAX_FAILS = int(os.getenv("LOGIN_MAX_FAILS", "5"))
LOGIN_WINDOW = int(os.getenv("LOGIN_WINDOW_SECONDS", "300"))
SEARCH_ALERT = int(os.getenv("SEARCH_ALERT_PER_MINUTE", "30"))

app = FastAPI(title="API NoSQL (endurecida)", docs_url=None, redoc_url=None)

# ---------------------------------------------------------------- modelos
USERNAME_RE = r"^[A-Za-z0-9_.\-]{1,32}$"
PRODUCTO_RE = r"^[\w\s\"'.,\-áéíóúüñÁÉÍÓÚÜÑ]{1,80}$"
SEARCH_RE = r"^[\w\sáéíóúüñÁÉÍÓÚÜÑ\-]{0,60}$"   # sin : * ? ~ ( ) " etc.


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: StrictStr = Field(pattern=USERNAME_RE)
    password: StrictStr = Field(min_length=1, max_length=128)


class Pedido(BaseModel):
    model_config = ConfigDict(extra="forbid")
    producto: StrictStr = Field(pattern=PRODUCTO_RE)
    cantidad: StrictInt = Field(ge=1, le=1000)


# ---------------------------------------------------------------- utilidades
_DUMMY_HASH = "pbkdf2_sha256$310000$00000000000000000000000000000000$" + "0" * 64


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        assert algo == "pbkdf2_sha256"
    except (ValueError, AssertionError):
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
    return hmac.compare_digest(calc.hex(), hash_hex)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


_login_fails: dict[str, deque] = defaultdict(deque)
_searches: dict[str, deque] = defaultdict(deque)


def _prune(q: deque, window: int, now: float):
    while q and now - q[0] > window:
        q.popleft()


def oid(pid: str) -> ObjectId:
    try:
        return ObjectId(pid)
    except (InvalidId, TypeError):
        raise HTTPException(400, "Identificador no válido")


def ser(doc):
    doc["_id"] = str(doc["_id"])
    return doc


# ---------------------------------------------------------------- middleware / handlers
@app.middleware("http")
async def access_log(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    sec_event(
        "http_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        ip=client_ip(request),
        ms=round((time.perf_counter() - t0) * 1000, 1),
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    # No se registra el valor enviado (podría contener credenciales)
    sec_event(
        "input_rejected",
        logging.WARNING,
        path=request.url.path,
        ip=client_ip(request),
        errors=[{"loc": e["loc"], "type": e["type"]} for e in exc.errors()],
    )
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(
            [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        )},
    )


# ---------------------------------------------------------------- endpoints
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/login_mongo")
def login(body: Login, request: Request):
    ip = client_ip(request)
    key = f"{ip}|{body.username}"
    now = time.time()
    fails = _login_fails[key]
    _prune(fails, LOGIN_WINDOW, now)
    if len(fails) >= LOGIN_MAX_FAILS:
        sec_event("login_blocked", logging.WARNING, ip=ip, username=body.username)
        raise HTTPException(429, "Demasiados intentos. Inténtalo más tarde.")

    user = db.usuarios.find_one({"username": body.username}, {"_id": 0})
    ok = verify_password(body.password, user["password_hash"] if user else _DUMMY_HASH) and user is not None
    if not ok:
        fails.append(now)
        sec_event("login_failed", logging.WARNING, ip=ip, username=body.username,
                  fails_in_window=len(fails))
        raise HTTPException(401, "Credenciales incorrectas")

    fails.clear()
    sec_event("login_ok", ip=ip, username=body.username)
    return {"msg": "ok", "user": {"username": user["username"], "rol": user["rol"]}}


# ---------- CRUD de pedidos ----------
@app.get("/pedidos")
def listar_pedidos(page: int = Query(1, ge=1, le=1000), size: int = Query(20, ge=1, le=50)):
    cur = db.pedidos.find().skip((page - 1) * size).limit(size)
    return [ser(p) for p in cur]


@app.post("/pedidos", status_code=201)
def crear_pedido(p: Pedido):
    res = db.pedidos.insert_one(p.model_dump())
    return {"_id": str(res.inserted_id), **p.model_dump()}


@app.get("/pedidos/{pid}")
def leer_pedido(pid: str):
    doc = db.pedidos.find_one({"_id": oid(pid)})
    if not doc:
        raise HTTPException(404, "No existe")
    return ser(doc)


@app.put("/pedidos/{pid}")
def actualizar_pedido(pid: str, p: Pedido):
    res = db.pedidos.update_one({"_id": oid(pid)}, {"$set": p.model_dump()})
    if res.matched_count == 0:
        raise HTTPException(404, "No existe")
    return {"_id": pid, **p.model_dump()}


@app.delete("/pedidos/{pid}")
def borrar_pedido(pid: str):
    res = db.pedidos.delete_one({"_id": oid(pid)})
    if res.deleted_count == 0:
        raise HTTPException(404, "No existe")
    return {"deleted": pid}


# ---------- Búsqueda controlada en Elasticsearch ----------
PUBLIC_FIELDS = ["nombre", "descripcion", "categoria", "precio"]


@app.get("/articulos")
def art_search(
    request: Request,
    q: str = Query("", max_length=60, pattern=SEARCH_RE),
    page: int = Query(1, ge=1, le=100),
    size: int = Query(10, ge=1, le=50),
):
    ip = client_ip(request)
    now = time.time()
    hist = _searches[ip]
    _prune(hist, 60, now)
    hist.append(now)
    if len(hist) == SEARCH_ALERT + 1:
        sec_event("search_spike", logging.WARNING, ip=ip, searches_last_minute=len(hist))

    query = {"multi_match": {"query": q, "fields": ["nombre^2", "descripcion"]}} if q.strip() \
        else {"match_all": {}}
    body = {"query": query, "from": (page - 1) * size, "size": size, "_source": PUBLIC_FIELDS}
    try:
        r = requests.post(f"{ES}/articulos/_search", json=body, auth=ES_AUTH, timeout=5)
        r.raise_for_status()
    except requests.RequestException:
        sec_event("search_backend_error", logging.ERROR, ip=ip)
        raise HTTPException(502, "Servicio de búsqueda no disponible")
    hits = r.json()["hits"]
    return {
        "total": hits["total"]["value"],
        "page": page,
        "size": size,
        "resultados": [h["_source"] for h in hits["hits"]],
    }
