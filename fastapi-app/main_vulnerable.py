"""API NoSQL VULNERABLE (baseline). Solo para laboratorio.

Fallos intencionados:
  * /login_mongo pasa el JSON del cliente tal cual al filtro de MongoDB (NoSQLi).
  * /articulos reenvía el parámetro q a Elasticsearch como query_string de Lucene
    y devuelve el documento completo (incluidos campos internos).
  * Conexiones a MongoDB y Elasticsearch sin autenticación ni TLS.
"""
import os

import requests
from bson import ObjectId
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from pymongo import MongoClient

app = FastAPI(title="API NoSQL (vulnerable)")
mongo = MongoClient(os.getenv("MONGO_HOST", "mongo"), 27017)
db = mongo[os.getenv("MONGO_DATABASE", "tienda")]
ES = f"http://{os.getenv('ELASTIC_HOST', 'elasticsearch')}:9200"


class Pedido(BaseModel):
    producto: str
    cantidad: int


def _ser(doc):
    doc["_id"] = str(doc["_id"])
    return doc


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/login_mongo")
async def login(req: Request):
    data = await req.json()
    # ❌ NoSQLi: acepta operadores $ en credenciales
    user = db.usuarios.find_one(
        {"username": data.get("username"), "password": data.get("password")},
        {"_id": 0, "password": 0},
    )
    if user:
        return {"msg": "ok", "user": user}
    raise HTTPException(status_code=401, detail="Bad creds")


# ---------- CRUD de pedidos ----------
@app.get("/pedidos")
def listar_pedidos():
    return [_ser(p) for p in db.pedidos.find()]


@app.post("/pedidos", status_code=201)
def crear_pedido(p: Pedido):
    res = db.pedidos.insert_one(p.model_dump())
    return {"_id": str(res.inserted_id), **p.model_dump()}


@app.get("/pedidos/{pid}")
def leer_pedido(pid: str):
    doc = db.pedidos.find_one({"_id": ObjectId(pid)})
    if not doc:
        raise HTTPException(404, "No existe")
    return _ser(doc)


@app.put("/pedidos/{pid}")
def actualizar_pedido(pid: str, p: Pedido):
    res = db.pedidos.update_one({"_id": ObjectId(pid)}, {"$set": p.model_dump()})
    if res.matched_count == 0:
        raise HTTPException(404, "No existe")
    return {"_id": pid, **p.model_dump()}


@app.delete("/pedidos/{pid}")
def borrar_pedido(pid: str):
    res = db.pedidos.delete_one({"_id": ObjectId(pid)})
    if res.deleted_count == 0:
        raise HTTPException(404, "No existe")
    return {"deleted": pid}


# ---------- Búsqueda en Elasticsearch ----------
@app.get("/articulos")
def art_search(q: str = "*"):
    # ❌ q se interpreta como sintaxis Lucene y se devuelve todo el _source
    return requests.get(f"{ES}/articulos/_search", params={"q": q}).json()
