from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
import httpx
import json
from fastapi.middleware.cors import CORSMiddleware
from database import engine, get_db
import models
from models import now_bogota_iso
import schemas

# Crea las tablas en SQLite automáticamente
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="StudyMatch - Servicio de Mensajería y Notificaciones",
    version="1.3",
    description="Microservicio de chat individual/grupal en tiempo real y alertas."
)

#Perimitir CORS para desarrollo local.

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
    x_user_id: Optional[int] = Header(default=None, alias="X-User-Id"),
    authorization: Optional[str] = Header(default=None)
) -> int:
    """
    Extrae el usuario_id inyectado por el API Gateway (X-User-Id) o del Bearer Token.
    Si no se envía ninguno, retorna 401 Unauthorized.
    """
    if x_user_id is not None and x_user_id > 0:
        return x_user_id
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        # En pruebas locales permitimos 'Bearer 12' o un token simulado
        if token.isdigit():
            return int(token)
        return 12  # Usuario por defecto si se envía un JWT de prueba
    raise HTTPException(
        status_code=401,
        detail="No Autorizado: Token de autenticación ausente, inválido o expirado."
    )

# ==========================================
# 1. GESTOR DE WEBSOCKETS (TIEMPO REAL)
# ==========================================
class ConnectionManager:
    def __init__(self):
        # Mapea usuario_id -> lista de conexiones WebSocket activas
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, usuario_id: int):
        await websocket.accept()
        if usuario_id not in self.active_connections:
            self.active_connections[usuario_id] = []
        self.active_connections[usuario_id].append(websocket)

    def disconnect(self, websocket: WebSocket, usuario_id: int):
        if usuario_id in self.active_connections:
            self.active_connections[usuario_id].remove(websocket)
            if not self.active_connections[usuario_id]:
                del self.active_connections[usuario_id]

    def is_connected(self, usuario_id: int) -> bool:
        return usuario_id in self.active_connections and len(self.active_connections[usuario_id]) > 0

    async def send_to_user(self, usuario_id: int, payload: dict):
        if self.is_connected(usuario_id):
            for ws in self.active_connections[usuario_id]:
                await ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def broadcast(self, payload: dict):
        for user_sockets in self.active_connections.values():
            for ws in user_sockets:
                await ws.send_text(json.dumps(payload, ensure_ascii=False))

manager = ConnectionManager()

@app.websocket("/api/v1/chat/ws")
async def chat_websocket(websocket: WebSocket, usuario_id: int = Query(default=12)):
    await manager.connect(websocket, usuario_id)
    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                data = {"evento": "nuevo_mensaje", "contenido": raw_data}
            data["remitente_id"] = usuario_id
            data["timestamp"] = now_bogota_iso()
            await manager.broadcast(data)
    except WebSocketDisconnect:
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
            # Regla Sección J: Si Matching no responde (Timeout/Caída), rechazar con 503
            raise HTTPException(
                status_code=503,
                detail="Servicio de Matching temporalmente no disponible (Timeout/Conexión). No se pudo validar el match."
            )

    nueva_conv = models.Conversacion(tipo="individual")
    db.add(nueva_conv)
    db.commit()
    db.refresh(nueva_conv)

    # Registrar ambos miembros
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
    nueva_conv = models.Conversacion(
        tipo="grupal",
        grupo_id=datos.grupo_id,
        nombre=datos.nombre
    )
    db.add(nueva_conv)
    db.commit()
    db.refresh(nueva_conv)

    for uid in set(datos.miembros):
        db.add(models.MiembroConversacion(conversacion_id=nueva_conv.conversacion_id, usuario_id=uid))
    db.commit()

    return {
        "conversacion_id": nueva_conv.conversacion_id,
        "tipo": "grupal",
        "grupo_id": nueva_conv.grupo_id,
        "nombre": nueva_conv.nombre,
        "miembros": list(set(datos.miembros)),
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
    if not conv:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")

    # Validar que el remitente sea miembro activo de la conversación
    miembro = db.query(models.MiembroConversacion).filter(
        models.MiembroConversacion.conversacion_id == id,
        models.MiembroConversacion.usuario_id == usuario_actual,
        models.MiembroConversacion.estado == "activo"
    ).first()
    if not miembro:
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

    respuesta = {
        "mensaje_id": nuevo_msg.mensaje_id,
        "conversacion_id": nuevo_msg.conversacion_id,
        "remitente_id": nuevo_msg.remitente_id,
        "contenido": nuevo_msg.contenido,
        "enviado_en": nuevo_msg.enviado_en,
        "estado": nuevo_msg.estado
    }

    # Notificar a los demás miembros por WebSocket o guardar Notificación en BD
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
    if not conv:
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

    resultado = []
    for memb in membresias:
        conv = db.query(models.Conversacion).filter(
            models.Conversacion.conversacion_id == memb.conversacion_id
        ).first()
        ultimo_msg = db.query(models.Mensaje).filter(
            models.Mensaje.conversacion_id == memb.conversacion_id
        ).order_by(models.Mensaje.mensaje_id.desc()).first()

        resultado.append({
            "conversacion_id": conv.conversacion_id,
            "tipo": conv.tipo,
            "nombre": conv.nombre,
            "ultimo_mensaje": ultimo_msg.contenido if ultimo_msg else None,
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
@app.get("/api/v1/usuarios/{id}/notificaciones")
async def listar_notificaciones(
    id: Optional[int] = None,
    x_user_id: Optional[int] = Header(default=None, alias="X-User-Id"),
    db: Session = Depends(get_db)
):
    target_user = id or x_user_id
    if not target_user:
        raise HTTPException(status_code=400, detail="Debe indicar el ID del usuario en la ruta o en la cabecera X-User-Id.")

    notificaciones = db.query(models.Notificacion).filter(
        models.Notificacion.usuario_id == target_user
    ).order_by(models.Notificacion.notificacion_id.desc()).all()
    return notificaciones

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
    if not msg:
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
    if not msg:
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
    if not conv:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    if conv.tipo == "individual":
        raise HTTPException(status_code=400, detail="No se pueden remover miembros de una conversación individual.")

    miembro = db.query(models.MiembroConversacion).filter(
        models.MiembroConversacion.conversacion_id == id,
        models.MiembroConversacion.usuario_id == usuario_id,
        models.MiembroConversacion.estado == "activo"
    ).first()
    if not miembro:
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