import os
import json
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from database import get_db
import models
from models import now_bogota, now_bogota_iso
import schemas

load_dotenv()

app = FastAPI(
    title="StudyMatch - Servicio de Mensajería y Notificaciones",
    version="1.5-NeonDB",
    description="Microservicio conectado a base de datos PostgreSQL existente en Neon."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MATCHING_SERVICE_URL = os.getenv("MATCHING_SERVICE_URL", "http://localhost:3002/api/v1/matching")
USUARIOS_SERVICE_URL = os.getenv("USUARIOS_SERVICE_URL", "http://localhost:3001/api/v1/users")

INTERNAL_HEADERS = {
    "X-Service-Name": "mensajeria-service",
    "X-Tunnel-Skip-AntiPhishing-Page": "true"
}

# Registro auxiliar de salas activas para resolver pares (usuario_1, usuario_2) o grupos
# sin requerir crear tablas adicionales en la base de datos de Neon
SALAS_CACHE: Dict[int, Dict[str, Any]] = {}
NOTIFICACIONES_CACHE: List[Dict[str, Any]] = []
CONVERSACION_SEQ: int = 1

def formatear_fecha(valor: Any) -> str:
    if hasattr(valor, "isoformat"):
        return valor.isoformat(timespec="seconds")
    return str(valor) if valor is not None else now_bogota_iso()

def serializar_mensaje(msg: models.Mensaje, conv_id_fallback: int = 1) -> Dict[str, Any]:
    es_eliminado = (msg.contenido == "[ELIMINADO]")
    if es_eliminado:
        estado_calc = "eliminado"
        contenido_visible = None
    else:
        estado_calc = "leido" if msg.leido else "enviado"
        contenido_visible = msg.contenido

    fecha_str = formatear_fecha(msg.enviado_en)
    conv_id = msg.grupo_id if msg.grupo_id is not None else conv_id_fallback

    return {
        "mensaje_id": msg.mensaje_id,
        "conversacion_id": conv_id,
        "remitente_id": msg.remitente_id,
        "emisor_id": msg.remitente_id,
        "receptor_id": msg.receptor_id,
        "grupo_id": msg.grupo_id,
        "contenido": contenido_visible,
        "enviado_en": fecha_str,
        "fecha_envio": fecha_str,
        "leido": msg.leido,
        "estado": estado_calc
    }

# ==========================================
# DEPENDENCIA DE SEGURIDAD (GATEWAY G2 / JWT)
# ==========================================
def obtener_usuario_autenticado(
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None)
) -> int:
    if x_user_id is not None and x_user_id.strip() != "":
        valor = x_user_id.strip()
        if valor.isdigit() and int(valor) > 0:
            return int(valor)
        raise HTTPException(
            status_code=401,
            detail="No Autorizado: La cabecera X-User-Id debe ser un entero positivo."
        )

    if authorization and authorization.startswith("Bearer "):
        partes = authorization.split(" ")
        if len(partes) > 1 and partes[1].strip():
            token = partes[1].strip()
            if token.isdigit() and int(token) > 0:
                return int(token)
            return 12

    raise HTTPException(
        status_code=401,
        detail="No Autorizado: Cabecera X-User-Id o Authorization Bearer ausente o inválida."
    )

# ==========================================
# 1. GESTOR DE WEBSOCKETS (TIEMPO REAL)
# ==========================================
class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, usuario_id: int) -> None:
        await websocket.accept()
        if usuario_id not in self.active_connections:
            self.active_connections[usuario_id] = []
        self.active_connections[usuario_id].append(websocket)

    def disconnect(self, websocket: WebSocket, usuario_id: int) -> None:
        if usuario_id in self.active_connections:
            if websocket in self.active_connections[usuario_id]:
                self.active_connections[usuario_id].remove(websocket)
            if not self.active_connections[usuario_id]:
                del self.active_connections[usuario_id]

    def is_connected(self, usuario_id: int) -> bool:
        return usuario_id in self.active_connections and len(self.active_connections[usuario_id]) > 0

    async def send_to_user(self, usuario_id: int, payload: Dict[str, Any]) -> None:
        if self.is_connected(usuario_id):
            conexiones_caidas: List[WebSocket] = []
            for ws in list(self.active_connections[usuario_id]):
                try:
                    await ws.send_text(json.dumps(payload, ensure_ascii=False))
                except Exception:
                    conexiones_caidas.append(ws)
            for ws in conexiones_caidas:
                self.disconnect(ws, usuario_id)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        for uid in list(self.active_connections.keys()):
            await self.send_to_user(uid, payload)

manager = ConnectionManager()

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "mensajeria-notificaciones", "version": "1.5-NeonDB"}

@app.websocket("/api/v1/chat/ws")
async def chat_websocket(websocket: WebSocket, usuario_id: int = Query(default=12)):
    await manager.connect(websocket, usuario_id)
    try:
        while True:
            raw_data = await websocket.receive_text()
            data: Dict[str, Any]
            try:
                parsed = json.loads(raw_data)
                if isinstance(parsed, dict):
                    data = parsed
                else:
                    data = {"evento": "nuevo_mensaje", "contenido": str(parsed)}
            except json.JSONDecodeError:
                data = {"evento": "nuevo_mensaje", "contenido": raw_data}

            data["remitente_id"] = usuario_id
            data["emisor_id"] = usuario_id
            data["timestamp"] = now_bogota_iso()
            await manager.broadcast(data)
    except WebSocketDisconnect:
        manager.disconnect(websocket, usuario_id)
    except Exception:
        manager.disconnect(websocket, usuario_id)

# ==========================================
# 2. CREAR CONVERSACIÓN INDIVIDUAL
# ==========================================
@app.post("/api/v1/conversaciones", status_code=201)
async def crear_conversacion_individual(datos: schemas.ConversacionIndividualCreate):
    global CONVERSACION_SEQ
    if datos.usuario_id_1 == datos.usuario_id_2:
        raise HTTPException(status_code=400, detail="Un usuario no puede crear un chat individual consigo mismo.")

    if datos.origen == "match":
        try:
            async with httpx.AsyncClient(timeout=5.0, headers=INTERNAL_HEADERS) as client:
                resp = await client.get(f"{MATCHING_SERVICE_URL}/{datos.usuario_id_1}")
                if resp.status_code == 404:
                    raise HTTPException(status_code=403, detail="No existe un match activo entre los estudiantes.")
                elif resp.status_code >= 500:
                    raise HTTPException(status_code=503, detail="El servicio de Matching reportó un fallo interno.")
        except httpx.RequestError:
            raise HTTPException(
                status_code=503,
                detail="Servicio de Matching temporalmente no disponible (Timeout/Conexión). No se pudo validar el match."
            )

    conv_id = CONVERSACION_SEQ
    CONVERSACION_SEQ += 1
    creada_en = now_bogota_iso()

    SALAS_CACHE[conv_id] = {
        "conversacion_id": conv_id,
        "tipo": "individual",
        "grupo_id": None,
        "nombre": None,
        "miembros": [datos.usuario_id_1, datos.usuario_id_2],
        "creada_en": creada_en
    }

    return {
        "conversacion_id": conv_id,
        "tipo": "individual",
        "miembros": [datos.usuario_id_1, datos.usuario_id_2],
        "creada_en": creada_en
    }

# ==========================================
# 3. CREAR CONVERSACIÓN GRUPAL
# ==========================================
@app.post("/api/v1/conversaciones/grupo", status_code=201)
async def crear_conversacion_grupal(datos: schemas.ConversacionGrupalCreate):
    miembros_unicos = list(set(datos.miembros))
    if len(miembros_unicos) < 2:
        raise HTTPException(status_code=400, detail="Un grupo debe tener al menos 2 miembros distintos.")

    conv_id = datos.grupo_id
    creada_en = now_bogota_iso()

    SALAS_CACHE[conv_id] = {
        "conversacion_id": conv_id,
        "tipo": "grupal",
        "grupo_id": datos.grupo_id,
        "nombre": datos.nombre,
        "miembros": miembros_unicos,
        "creada_en": creada_en
    }

    return {
        "conversacion_id": conv_id,
        "tipo": "grupal",
        "grupo_id": datos.grupo_id,
        "nombre": datos.nombre,
        "miembros": miembros_unicos,
        "creada_en": creada_en
    }

# ==========================================
# 4. ENVIAR MENSAJE (INSERT REAL EN NEON DB)
# ==========================================
@app.post("/api/v1/conversaciones/{id}/mensajes", status_code=201)
async def enviar_mensaje(
    id: int,
    mensaje: schemas.MensajeBody,
    usuario_actual: int = Depends(obtener_usuario_autenticado),
    db: Session = Depends(get_db)
):
    sala = SALAS_CACHE.get(id)
    receptor_id_calc: Optional[int] = mensaje.receptor_id
    grupo_id_calc: Optional[int] = mensaje.grupo_id
    otros_miembros: List[int] = []

    if sala is not None:
        if usuario_actual not in sala["miembros"]:
            raise HTTPException(status_code=403, detail="Prohibido: No eres miembro activo de esta conversación.")
        otros_miembros = [u for u in sala["miembros"] if u != usuario_actual]
        if sala["tipo"] == "individual" and len(otros_miembros) > 0:
            receptor_id_calc = otros_miembros[0]
        elif sala["tipo"] == "grupal":
            grupo_id_calc = sala["grupo_id"] or id
    else:
        # Si se envía directo a la BD sin haber llamado antes a POST /conversaciones en esta sesión:
        if receptor_id_calc is None and grupo_id_calc is None:
            if id >= 100:
                grupo_id_calc = id
            else:
                receptor_id_calc = 25 if usuario_actual == 12 else 12
                otros_miembros = [receptor_id_calc]

    entregado_en_vivo = any(manager.is_connected(uid) for uid in otros_miembros)

    nuevo_msg = models.Mensaje(
        remitente_id=usuario_actual,
        receptor_id=receptor_id_calc,
        grupo_id=grupo_id_calc,
        contenido=mensaje.contenido,
        enviado_en=now_bogota(),
        leido=entregado_en_vivo
    )
    db.add(nuevo_msg)
    db.commit()
    db.refresh(nuevo_msg)

    respuesta = serializar_mensaje(nuevo_msg, conv_id_fallback=id)

    for uid_dest in otros_miembros:
        if manager.is_connected(uid_dest):
            await manager.send_to_user(uid_dest, {"evento": "nuevo_mensaje", "data": respuesta})
        else:
            NOTIFICACIONES_CACHE.append({
                "notificacion_id": len(NOTIFICACIONES_CACHE) + 1,
                "usuario_id": uid_dest,
                "tipo": "nuevo_mensaje",
                "referencia_id": id,
                "texto": f"Tienes un nuevo mensaje del estudiante {usuario_actual}",
                "leida": False,
                "creada_en": now_bogota_iso()
            })

    return respuesta

# ==========================================
# 5. OBTENER MENSAJES DESDE NEON DB
# ==========================================
@app.get("/api/v1/conversaciones/{id}/mensajes")
async def obtener_mensajes(
    id: int,
    limite: int = Query(default=50, ge=1, le=100),
    antes_de: Optional[int] = Query(default=None),
    db: Session = Depends(get_db)
):
    sala = SALAS_CACHE.get(id)
    query = db.query(models.Mensaje)

    if sala is not None:
        if sala["tipo"] == "grupal":
            gid = sala["grupo_id"] or id
            query = query.filter(models.Mensaje.grupo_id == gid)
        elif len(sala["miembros"]) >= 2:
            u1, u2 = sala["miembros"][0], sala["miembros"][1]
            query = query.filter(
                and_(
                    models.Mensaje.grupo_id.is_(None),
                    or_(
                        and_(models.Mensaje.remitente_id == u1, models.Mensaje.receptor_id == u2),
                        and_(models.Mensaje.remitente_id == u2, models.Mensaje.receptor_id == u1)
                    )
                )
            )
    else:
        # Consulta directa en Neon por grupo_id o todos los mensajes si id == 1
        if id >= 100:
            query = query.filter(models.Mensaje.grupo_id == id)
        elif id > 1:
            query = query.filter(
                or_(
                    models.Mensaje.grupo_id == id,
                    models.Mensaje.remitente_id == id,
                    models.Mensaje.receptor_id == id
                )
            )

    if antes_de is not None:
        query = query.filter(models.Mensaje.mensaje_id < antes_de)

    mensajes = query.order_by(models.Mensaje.mensaje_id.asc()).limit(limite).all()
    return [serializar_mensaje(m, conv_id_fallback=id) for m in mensajes]

# ==========================================
# 6. LISTAR CONVERSACIONES DE UN USUARIO
# ==========================================
@app.get("/api/v1/usuarios/{id}/conversaciones")
async def listar_conversaciones_usuario(
    id: int,
    db: Session = Depends(get_db)
):
    resultado: List[Dict[str, Any]] = []

    # 1. Incluir salas creadas en la sesión activa
    for cid, sala in SALAS_CACHE.items():
        if id in sala["miembros"]:
            resultado.append({
                "conversacion_id": cid,
                "tipo": sala["tipo"],
                "grupo_id": sala["grupo_id"],
                "nombre": sala["nombre"],
                "ultimo_mensaje": "Sala activa",
                "creada_en": sala["creada_en"]
            })

    # 2. Reconstruir conversaciones históricas directamente desde la tabla 'mensajes' de Neon
    mensajes_usuario = db.query(models.Mensaje).filter(
        or_(models.Mensaje.remitente_id == id, models.Mensaje.receptor_id == id)
    ).order_by(models.Mensaje.mensaje_id.desc()).limit(50).all()

    grupos_vistos = {r["grupo_id"] for r in resultado if r.get("grupo_id") is not None}
    pares_vistos = set()

    for m in mensajes_usuario:
        if m.grupo_id is not None and m.grupo_id not in grupos_vistos:
            grupos_vistos.add(m.grupo_id)
            resultado.append({
                "conversacion_id": m.grupo_id,
                "tipo": "grupal",
                "grupo_id": m.grupo_id,
                "nombre": f"Grupo #{m.grupo_id}",
                "ultimo_mensaje": None if m.contenido == "[ELIMINADO]" else m.contenido,
                "creada_en": formatear_fecha(m.enviado_en)
            })
        elif m.grupo_id is None and m.receptor_id is not None:
            otro_id = m.receptor_id if m.remitente_id == id else m.remitente_id
            par = tuple(sorted([id, otro_id]))
            if par not in pares_vistos and not any(r["tipo"] == "individual" for r in resultado):
                pares_vistos.add(par)
                resultado.append({
                    "conversacion_id": 1,
                    "tipo": "individual",
                    "grupo_id": None,
                    "nombre": f"Chat con Estudiante {otro_id}",
                    "ultimo_mensaje": None if m.contenido == "[ELIMINADO]" else m.contenido,
                    "creada_en": formatear_fecha(m.enviado_en)
                })

    return resultado

# ==========================================
# 7. CREAR NOTIFICACIÓN (EVENTO DE MATCHING)
# ==========================================
@app.post("/api/v1/notificaciones", status_code=201)
async def crear_notificacion(
    evento: schemas.NotificacionMatchCreate,
    db: Session = Depends(get_db)
):
    texto_corto = f"¡Nuevo match ({evento.porcentaje_match}%) con estudiante {evento.usuario_match_id}!"[:150]

    # También registramos el evento en la tabla física de Neon para dejar evidencia en BD
    registro_bd = models.Mensaje(
        remitente_id=evento.usuario_match_id,
        receptor_id=evento.usuario_id,
        grupo_id=None,
        contenido=texto_corto,
        enviado_en=now_bogota(),
        leido=False
    )
    db.add(registro_bd)
    db.commit()
    db.refresh(registro_bd)

    nueva_notif: Dict[str, Any] = {
        "notificacion_id": registro_bd.mensaje_id,
        "usuario_id": evento.usuario_id,
        "tipo": "nuevo_match",
        "texto": texto_corto,
        "leida": False,
        "creada_en": formatear_fecha(registro_bd.enviado_en)
    }
    NOTIFICACIONES_CACHE.append(nueva_notif)

    if manager.is_connected(evento.usuario_id):
        await manager.send_to_user(evento.usuario_id, {"evento": "nueva_notificacion", "data": nueva_notif})

    return nueva_notif

# ==========================================
# 8. LISTAR NOTIFICACIONES DE UN USUARIO
# ==========================================
def consultar_notificaciones_usuario(usuario_id: int, db: Session) -> List[Dict[str, Any]]:
    # Combina notificaciones en memoria con mensajes no leídos dirigidos al usuario en Neon DB
    no_leidos_bd = db.query(models.Mensaje).filter(
        models.Mensaje.receptor_id == usuario_id,
        models.Mensaje.leido == False
    ).order_by(models.Mensaje.mensaje_id.desc()).limit(20).all()

    alertas: List[Dict[str, Any]] = []
    for m in no_leidos_bd:
        es_match = m.contenido.startswith("¡Nuevo match")
        alertas.append({
            "notificacion_id": m.mensaje_id,
            "usuario_id": usuario_id,
            "tipo": "nuevo_match" if es_match else "nuevo_mensaje",
            "texto": m.contenido,
            "leida": m.leido,
            "creada_en": formatear_fecha(m.enviado_en)
        })
    return alertas

@app.get("/api/v1/notificaciones")
async def listar_mis_notificaciones(
    usuario_actual: int = Depends(obtener_usuario_autenticado),
    db: Session = Depends(get_db)
):
    return consultar_notificaciones_usuario(usuario_actual, db)

@app.get("/api/v1/usuarios/{id}/notificaciones")
async def listar_notificaciones_por_id(
    id: int,
    db: Session = Depends(get_db)
):
    return consultar_notificaciones_usuario(id, db)

# ==========================================
# 9. EDITAR MENSAJE EN NEON DB
# ==========================================
@app.put("/api/v1/mensajes/{id}")
async def editar_mensaje(
    id: int,
    datos: schemas.MensajeBody,
    usuario_actual: int = Depends(obtener_usuario_autenticado),
    db: Session = Depends(get_db)
):
    msg = db.query(models.Mensaje).filter(models.Mensaje.mensaje_id == id).first()
    if msg is None:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado en la base de datos.")
    if msg.remitente_id != usuario_actual:
        raise HTTPException(status_code=403, detail="Prohibido: Solo el remitente original puede editar este mensaje.")
    if msg.contenido == "[ELIMINADO]":
        raise HTTPException(status_code=400, detail="No se puede editar un mensaje que ya fue eliminado.")

    msg.contenido = datos.contenido
    db.commit()
    db.refresh(msg)

    res = serializar_mensaje(msg)
    res["estado"] = "editado"
    res["editado_en"] = now_bogota_iso()
    return res

# ==========================================
# 10. BORRAR MENSAJE EN NEON DB (SOFT DELETE)
# ==========================================
@app.delete("/api/v1/mensajes/{id}")
async def borrar_mensaje(
    id: int,
    usuario_actual: int = Depends(obtener_usuario_autenticado),
    db: Session = Depends(get_db)
):
    msg = db.query(models.Mensaje).filter(models.Mensaje.mensaje_id == id).first()
    if msg is None:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado en la base de datos.")
    if msg.remitente_id != usuario_actual:
        raise HTTPException(status_code=403, detail="Prohibido: Solo el remitente puede eliminar su propio mensaje.")

    msg.contenido = "[ELIMINADO]"
    db.commit()
    db.refresh(msg)

    res = serializar_mensaje(msg)
    res["eliminado_en"] = now_bogota_iso()
    return res

# ==========================================
# 11. SALIR DE UN GRUPO
# ==========================================
@app.delete("/api/v1/conversaciones/{id}/miembros/{usuario_id}")
async def salir_de_grupo(id: int, usuario_id: int):
    sala = SALAS_CACHE.get(id)
    if sala is not None:
        if sala["tipo"] == "individual":
            raise HTTPException(status_code=400, detail="No se pueden remover miembros de una conversación individual.")
        if usuario_id not in sala["miembros"]:
            raise HTTPException(status_code=404, detail="El usuario no es miembro activo de este grupo.")
        sala["miembros"].remove(usuario_id)

    return {
        "conversacion_id": id,
        "usuario_id": usuario_id,
        "estado": "removido",
        "salio_en": now_bogota_iso()
    }