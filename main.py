from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional
import httpx
import json

from database import engine, get_db
import models
from models import now_bogota_iso
import schemas

# Crea las tablas en SQLite automáticamente si no existen
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="StudyMatch - Servicio de Mensajería y Notificaciones",
    version="1.3",
    description="Microservicio de chat individual/grupal en tiempo real y alertas."
)

# Permitir CORS para desarrollo local, Hoppscotch Web y Dev Tunnels
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# URLs de servicios externos (Apuntan a Mockoon en desarrollo local)
MATCHING_SERVICE_URL = "http://localhost:3002/api/v1/matching"
USUARIOS_SERVICE_URL = "http://localhost:3001/api/v1/users"

# ==========================================
# DEPENDENCIA DE SEGURIDAD (GATEWAY / JWT)
# ==========================================
def obtener_usuario_autenticado(
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None)
) -> int:
    """
    Extrae el usuario_id inyectado por el API Gateway (X-User-Id) o del Bearer Token.
    Si no se envía ninguno o es inválido, retorna 401 Unauthorized.
    """
    if x_user_id is not None and x_user_id.strip() != "":
        valor = x_user_id.strip()
        if valor.isdigit() and int(valor) > 0:
            return int(valor)
        raise HTTPException(
            status_code=401,
            detail="No Autorizado: La cabecera X-User-Id debe ser un número entero positivo."
        )

    if authorization and authorization.startswith("Bearer "):
        partes = authorization.split(" ")
        if len(partes) > 1 and partes[1].strip():
            token = partes[1].strip()
            if token.isdigit() and int(token) > 0:
                return int(token)
            return 12  # Usuario por defecto si se envía un JWT simulado

    raise HTTPException(
        status_code=401,
        detail="No Autorizado: Token de autenticación o cabecera X-User-Id ausente, inválido o expirado."
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
            data["timestamp"] = now_bogota_iso()
            await manager.broadcast(data)
    except WebSocketDisconnect:
        manager.disconnect(websocket, usuario_id)
    except Exception:
        manager.disconnect(websocket, usuario_id)

# ==========================================
# 2. CREAR CONVERSACIÓN INDIVIDUAL (CON HTTPX)
# ==========================================
@app.post("/api/v1/conversaciones", status_code=201)
async def crear_conversacion_individual(
    datos: schemas.ConversacionIndividualCreate,
    db: Session = Depends(get_db)
):
    if datos.usuario_id_1 == datos.usuario_id_2:
        raise HTTPException(status_code=400, detail="Un usuario no puede crear un chat individual consigo mismo.")

    # Si el origen es "match", validamos contra el microservicio de Matching (Grupo 1)
    if datos.origen == "match":
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
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

    nueva_conv = models.Conversacion(tipo="individual")
    db.add(nueva_conv)
    db.commit()
    db.refresh(nueva_conv)

    for uid in [datos.usuario_id_1, datos.usuario_id_2]:
        db.add(models.MiembroConversacion(conversacion_id=nueva_conv.conversacion_id, usuario_id=uid))
    db.commit()

    return {
        "conversacion_id": nueva_conv.conversacion_id,
        "tipo": nueva_conv.tipo,
        "miembros": [datos.usuario_id_1, datos.usuario_id_2],
        "creada_en": nueva_conv.creada_en
    }

# ==========================================
# 3. CREAR CONVERSACIÓN GRUPAL
# ==========================================
@app.post("/api/v1/conversaciones/grupo", status_code=201)
async def crear_conversacion_grupal(
    datos: schemas.ConversacionGrupalCreate,
    db: Session = Depends(get_db)
):
    miembros_unicos = list(set(datos.miembros))
    if len(miembros_unicos) < 2:
        raise HTTPException(status_code=400, detail="Un grupo debe tener al menos 2 miembros distintos.")

    nueva_conv = models.Conversacion(
        tipo="grupal",
        grupo_id=datos.grupo_id,
        nombre=datos.nombre
    )
    db.add(nueva_conv)
    db.commit()
    db.refresh(nueva_conv)

    for uid in miembros_unicos:
        db.add(models.MiembroConversacion(conversacion_id=nueva_conv.conversacion_id, usuario_id=uid))
    db.commit()

    return {
        "conversacion_id": nueva_conv.conversacion_id,
        "tipo": "grupal",
        "grupo_id": nueva_conv.grupo_id,
        "nombre": nueva_conv.nombre,
        "miembros": miembros_unicos,
        "creada_en": nueva_conv.creada_en
    }

# ==========================================
# 4. ENVIAR MENSAJE (REST + WEBSOCKET + NOTIFICACIÓN)
# ==========================================
@app.post("/api/v1/conversaciones/{id}/mensajes", status_code=201)
async def enviar_mensaje(
    id: int,
    mensaje: schemas.MensajeBody,
    usuario_actual: int = Depends(obtener_usuario_autenticado),
    db: Session = Depends(get_db)
):
    conv = db.query(models.Conversacion).filter(models.Conversacion.conversacion_id == id).first()
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")

    miembro = db.query(models.MiembroConversacion).filter(
        models.MiembroConversacion.conversacion_id == id,
        models.MiembroConversacion.usuario_id == usuario_actual,
        models.MiembroConversacion.estado == "activo"
    ).first()
    if miembro is None:
        raise HTTPException(status_code=403, detail="Prohibido: No eres miembro activo de esta conversación.")

    nuevo_msg = models.Mensaje(
        conversacion_id=id,
        remitente_id=usuario_actual,
        contenido=mensaje.contenido,
        estado="enviado"
    )
    db.add(nuevo_msg)
    db.commit()
    db.refresh(nuevo_msg)

    respuesta: Dict[str, Any] = {
        "mensaje_id": nuevo_msg.mensaje_id,
        "conversacion_id": nuevo_msg.conversacion_id,
        "remitente_id": nuevo_msg.remitente_id,
        "contenido": nuevo_msg.contenido,
        "enviado_en": nuevo_msg.enviado_en,
        "estado": nuevo_msg.estado
    }

    otros_miembros = db.query(models.MiembroConversacion).filter(
        models.MiembroConversacion.conversacion_id == id,
        models.MiembroConversacion.usuario_id != usuario_actual,
        models.MiembroConversacion.estado == "activo"
    ).all()

    for m in otros_miembros:
        if manager.is_connected(m.usuario_id):
            await manager.send_to_user(m.usuario_id, {"evento": "nuevo_mensaje", "data": respuesta})
        else:
            alerta = models.Notificacion(
                usuario_id=m.usuario_id,
                tipo="nuevo_mensaje",
                referencia_id=id,
                texto=f"Tienes un nuevo mensaje del usuario {usuario_actual}"
            )
            db.add(alerta)
    db.commit()

    return respuesta

# ==========================================
# 5. OBTENER MENSAJES DE UNA CONVERSACIÓN
# ==========================================
@app.get("/api/v1/conversaciones/{id}/mensajes")
async def obtener_mensajes(
    id: int,
    limite: int = Query(default=50, ge=1, le=100),
    antes_de: Optional[int] = Query(default=None),
    db: Session = Depends(get_db)
):
    conv = db.query(models.Conversacion).filter(models.Conversacion.conversacion_id == id).first()
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")

    query = db.query(models.Mensaje).filter(models.Mensaje.conversacion_id == id)
    if antes_de is not None:
        query = query.filter(models.Mensaje.mensaje_id < antes_de)

    mensajes = query.order_by(models.Mensaje.mensaje_id.asc()).limit(limite).all()
    return mensajes

# ==========================================
# 6. LISTAR CONVERSACIONES DE UN USUARIO
# ==========================================
@app.get("/api/v1/usuarios/{id}/conversaciones")
async def listar_conversaciones_usuario(
    id: int,
    pagina: int = Query(default=1, ge=1),
    limite: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    membresias = db.query(models.MiembroConversacion).filter(
        models.MiembroConversacion.usuario_id == id,
        models.MiembroConversacion.estado == "activo"
    ).offset((pagina - 1) * limite).limit(limite).all()

    resultado: List[Dict[str, Any]] = []
    for memb in membresias:
        conv = db.query(models.Conversacion).filter(
            models.Conversacion.conversacion_id == memb.conversacion_id
        ).first()
        if conv is None:
            continue

        ultimo_msg = db.query(models.Mensaje).filter(
            models.Mensaje.conversacion_id == memb.conversacion_id
        ).order_by(models.Mensaje.mensaje_id.desc()).first()

        resultado.append({
            "conversacion_id": conv.conversacion_id,
            "tipo": conv.tipo,
            "nombre": conv.nombre,
            "ultimo_mensaje": ultimo_msg.contenido if ultimo_msg is not None else None,
            "creada_en": conv.creada_en
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
    nueva_notif = models.Notificacion(
        usuario_id=evento.usuario_id,
        tipo="nuevo_match",
        referencia_id=evento.match_id,
        texto=f"¡Tienes un nuevo match ({evento.porcentaje_match}% de compatibilidad) con el estudiante {evento.usuario_match_id}!"
    )
    db.add(nueva_notif)
    db.commit()
    db.refresh(nueva_notif)
    return {
        "notificacion_id": nueva_notif.notificacion_id,
        "usuario_id": nueva_notif.usuario_id,
        "tipo": nueva_notif.tipo,
        "texto": nueva_notif.texto,
        "leida": nueva_notif.leida,
        "creada_en": nueva_notif.creada_en
    }

# ==========================================
# 8. LISTAR NOTIFICACIONES DE UN USUARIO
# ==========================================
@app.get("/api/v1/notificaciones")
async def listar_mis_notificaciones(
    usuario_actual: int = Depends(obtener_usuario_autenticado),
    db: Session = Depends(get_db)
):
    return db.query(models.Notificacion).filter(
        models.Notificacion.usuario_id == usuario_actual
    ).order_by(models.Notificacion.notificacion_id.desc()).all()

@app.get("/api/v1/usuarios/{id}/notificaciones")
async def listar_notificaciones_por_id(
    id: int,
    db: Session = Depends(get_db)
):
    return db.query(models.Notificacion).filter(
        models.Notificacion.usuario_id == id
    ).order_by(models.Notificacion.notificacion_id.desc()).all()

# ==========================================
# 9. EDITAR MENSAJE
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
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")
    if msg.remitente_id != usuario_actual:
        raise HTTPException(status_code=403, detail="Prohibido: Solo el remitente original puede editar este mensaje.")
    if msg.estado == "eliminado":
        raise HTTPException(status_code=400, detail="No se puede editar un mensaje que ya fue eliminado.")

    msg.contenido = datos.contenido
    msg.estado = "editado"
    msg.editado_en = now_bogota_iso()
    db.commit()
    db.refresh(msg)

    return {
        "mensaje_id": msg.mensaje_id,
        "contenido": msg.contenido,
        "estado": msg.estado,
        "editado_en": msg.editado_en
    }

# ==========================================
# 10. BORRAR MENSAJE (SOFT DELETE)
# ==========================================
@app.delete("/api/v1/mensajes/{id}")
async def borrar_mensaje(
    id: int,
    usuario_actual: int = Depends(obtener_usuario_autenticado),
    db: Session = Depends(get_db)
):
    msg = db.query(models.Mensaje).filter(models.Mensaje.mensaje_id == id).first()
    if msg is None:
        raise HTTPException(status_code=404, detail="Mensaje no encontrado.")
    if msg.remitente_id != usuario_actual:
        raise HTTPException(status_code=403, detail="Prohibido: Solo el remitente puede eliminar su propio mensaje.")

    msg.contenido = None
    msg.estado = "eliminado"
    msg.eliminado_en = now_bogota_iso()
    db.commit()
    db.refresh(msg)

    return {
        "mensaje_id": msg.mensaje_id,
        "estado": msg.estado,
        "contenido": msg.contenido,
        "eliminado_en": msg.eliminado_en
    }

# ==========================================
# 11. SALIR DE UN GRUPO
# ==========================================
@app.delete("/api/v1/conversaciones/{id}/miembros/{usuario_id}")
async def salir_de_grupo(
    id: int,
    usuario_id: int,
    db: Session = Depends(get_db)
):
    conv = db.query(models.Conversacion).filter(models.Conversacion.conversacion_id == id).first()
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    if conv.tipo == "individual":
        raise HTTPException(status_code=400, detail="No se pueden remover miembros de una conversación individual.")

    miembro = db.query(models.MiembroConversacion).filter(
        models.MiembroConversacion.conversacion_id == id,
        models.MiembroConversacion.usuario_id == usuario_id,
        models.MiembroConversacion.estado == "activo"
    ).first()
    if miembro is None:
        raise HTTPException(status_code=404, detail="El usuario no es miembro activo de este grupo.")

    miembro.estado = "removido"
    miembro.salio_en = now_bogota_iso()
    db.commit()

    return {
        "conversacion_id": id,
        "usuario_id": usuario_id,
        "estado": "removido",
        "salio_en": miembro.salio_en
    }