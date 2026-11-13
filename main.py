import json
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from models import now_bogota, now_bogota_iso

load_dotenv(override=True)

app = FastAPI(
    title="StudyMatch - Servicio de Mensajería y Notificaciones",
    version="2.1-NeonStrict",
    description="Microservicio en producción sincronizado."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MATCHING_SERVICE_URL = os.getenv("MATCHING_SERVICE_URL", "https://servicio-matching.onrender.com/api/v1/matching").strip()
USUARIOS_SERVICE_URL = os.getenv("USUARIOS_SERVICE_URL", "http://localhost:3001/api/v1/users")

INTERNAL_HEADERS = {
    "X-Service-Name": "mensajeria-service",
    "X-Tunnel-Skip-AntiPhishing-Page": "true"
}

PARES_INDIVIDUALES: Dict[int, List[int]] = {}
SEQ_INDIVIDUAL: int = 1

def fmt_fecha(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat(timespec="seconds")
    if isinstance(val, date):
        return val.isoformat()
    return str(val)

def serializar_mensaje(msg: models.Mensaje, conv_id_ref: Optional[int] = None) -> Dict[str, Any]:
    es_eliminado = (msg.estado == "eliminado" or msg.contenido is None)
    contenido_visible = None if es_eliminado else msg.contenido
    estado_actual = msg.estado or ("leido" if msg.leido else "enviado")
    conv_id = msg.grupo_id or conv_id_ref or msg.receptor_id or 1

    return {
        "mensaje_id": msg.mensaje_id,
        "conversacion_id": conv_id,
        "remitente_id": msg.remitente_id,
        "emisor_id": msg.remitente_id,
        "receptor_id": msg.receptor_id,
        "grupo_id": msg.grupo_id,
        "contenido": contenido_visible,
        "enviado_en": fmt_fecha(msg.enviado_en),
        "fecha_envio": fmt_fecha(msg.enviado_en),
        "leido": msg.leido,
        "estado": estado_actual,
        "editado_en": fmt_fecha(msg.editado_en),
        "eliminado_en": fmt_fecha(msg.eliminado_en)
    }

@app.get("/", include_in_schema=False)
async def interfaz_web():
    if os.path.exists("cliente_chat.html"):
        return FileResponse("cliente_chat.html")
    return {"service": "StudyMatch Mensajería y Notificaciones", "docs": "/docs"}

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "mensajeria-notificaciones", "database": "Neon PostgreSQL"}

@app.get("/api/v1/datos-iniciales")
async def ver_datos_existentes_en_neon(db: Session = Depends(get_db)):
    usuarios = db.query(models.Usuario).order_by(models.Usuario.usuario_id.asc()).limit(25).all()
    grupos = db.query(models.Grupo).order_by(models.Grupo.grupo_id.asc()).limit(25).all()
    return {
        "usuarios_disponibles": [
            {"usuario_id": u.usuario_id, "nombre": f"{u.nombre} {u.apellido or ''}".strip(), "correo": u.correo}
            for u in usuarios
        ],
        "grupos_disponibles": [
            {"grupo_id": g.grupo_id, "nombre": g.nombre, "materia_id": g.materia_id}
            for g in grupos
        ]
    }

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
            return 1

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
            caidas: List[WebSocket] = []
            for ws in list(self.active_connections[usuario_id]):
                try:
                    await ws.send_text(json.dumps(payload, ensure_ascii=False))
                except Exception:
                    caidas.append(ws)
            for ws in caidas:
                self.disconnect(ws, usuario_id)

    async def broadcast(self, payload: Dict[str, Any]) -> None:
        for uid in list(self.active_connections.keys()):
            await self.send_to_user(uid, payload)

manager = ConnectionManager()

@app.websocket("/api/v1/chat/ws")
async def chat_websocket(websocket: WebSocket, usuario_id: int = Query(default=1)):
    await manager.connect(websocket, usuario_id)
    try:
        while True:
            raw_data = await websocket.receive_text()
            data: Dict[str, Any]
            try:
                parsed = json.loads(raw_data)
                data = parsed if isinstance(parsed, dict) else {"evento": "nuevo_mensaje", "contenido": str(parsed)}
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
async def crear_conversacion_individual(
    datos: schemas.ConversacionIndividualCreate,
    db: Session = Depends(get_db)
):
    global SEQ_INDIVIDUAL
    if datos.usuario_id_1 == datos.usuario_id_2:
        raise HTTPException(
            status_code=400,
            detail=f"Selecciona un compañero distinto: tienes el mismo ID ({datos.usuario_id_1}) en Sesión y en Iniciar Chat."
        )

    u1 = db.query(models.Usuario).filter(models.Usuario.usuario_id == datos.usuario_id_1).first()
    u2 = db.query(models.Usuario).filter(models.Usuario.usuario_id == datos.usuario_id_2).first()
    if u1 is None or u2 is None:
        raise HTTPException(
            status_code=404,
            detail=f"Uno o ambos usuarios ({datos.usuario_id_1}, {datos.usuario_id_2}) no existen en Neon."
        )

    if datos.origen == "match":
        match_confirmado = False
        nombre_u2 = f"{u2.nombre} {u2.apellido or ''}".strip()

        # 1. Verificar si ya tienen un match registrado en Neon (por nombre o por texto previo)
        notifs_match = db.query(models.Notificacion).filter(
            models.Notificacion.usuario_id == datos.usuario_id_1,
            models.Notificacion.tipo == "nuevo_match"
        ).all()
        for n in notifs_match:
            txt = (n.texto or "").lower()
            if (
                nombre_u2.lower() in txt
                or u2.nombre.lower() in txt
                or f"estudiante {datos.usuario_id_2}" in txt
            ):
                match_confirmado = True
                break

        # 2. Si no está en notificaciones locales, consultar el servicio de Matching en Render
        if not match_confirmado:
            try:
                async with httpx.AsyncClient(timeout=20.0, headers=INTERNAL_HEADERS, follow_redirects=True) as client:
                    url_consulta = f"{MATCHING_SERVICE_URL}/{datos.usuario_id_1}"
                    resp = await client.get(url_consulta)
                    if resp.status_code == 200:
                        data_match = resp.json()
                        coincidencias = data_match.get("coincidencias", []) if isinstance(data_match, dict) else []
                        if any(c.get("usuario_match_id") == datos.usuario_id_2 for c in coincidencias):
                            match_confirmado = True
                    elif resp.status_code >= 500:
                        raise HTTPException(status_code=503, detail="El servicio de Matching en Render reportó un fallo interno.")
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"No se pudo conectar al servicio de Matching: {str(e)}"
                )

        if not match_confirmado:
            raise HTTPException(
                status_code=403,
                detail=f"No existe un match activo entre {u1.nombre} y {nombre_u2}."
            )

    conv_id = datos.usuario_id_2
    PARES_INDIVIDUALES[SEQ_INDIVIDUAL] = [datos.usuario_id_1, datos.usuario_id_2]
    SEQ_INDIVIDUAL += 1

    return {
        "conversacion_id": conv_id,
        "tipo": "individual",
        "miembros": [datos.usuario_id_1, datos.usuario_id_2],
        "creada_en": now_bogota_iso()
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

    # Verificar que los usuarios existan antes de asociarlos
    usuarios_existentes = db.query(models.Usuario.usuario_id).filter(
        models.Usuario.usuario_id.in_(miembros_unicos)
    ).all()
    ids_validos = [u[0] for u in usuarios_existentes]
    if len(ids_validos) < 2:
        raise HTTPException(
            status_code=404,
            detail=f"Al menos 2 miembros deben existir en la tabla 'usuarios' de Neon. Encontrados: {ids_validos}"
        )

    # Buscar si el grupo ya existe (solo si se envió un grupo_id)
    grupo = None
    if datos.grupo_id is not None:
        grupo = db.query(models.Grupo).filter(models.Grupo.grupo_id == datos.grupo_id).first()

    # Si no existe, lo creamos SIN pasar 'grupo_id' para que lo genere automáticamente
    if grupo is None:
        materia_default = db.query(models.Materia).first()
        grupo = models.Grupo(
            nombre=datos.nombre,
            materia_id=materia_default.materia_id if materia_default else None
        )
        db.add(grupo)
        db.commit()
        db.refresh(grupo)

    for idx, uid in enumerate(ids_validos):
        existe_miembro = db.query(models.GrupoUsuario).filter(
            models.GrupoUsuario.grupo_id == grupo.grupo_id,
            models.GrupoUsuario.usuario_id == uid
        ).first()
        if existe_miembro is None:
            rol_permitido = "admin" if idx == 0 else "usuario"
            db.add(models.GrupoUsuario(grupo_id=grupo.grupo_id, usuario_id=uid, rol=rol_permitido))
    db.commit()

    return {
        "conversacion_id": grupo.grupo_id,
        "tipo": "grupal",
        "grupo_id": grupo.grupo_id,
        "nombre": grupo.nombre,
        "miembros": ids_validos,
        "creada_en": fmt_fecha(grupo.fecha_creacion) or now_bogota_iso()
    }

# ==========================================
# 4. ENVIAR MENSAJE (CUMPLIENDO CHECKS)
# ==========================================
@app.post("/api/v1/conversaciones/{id}/mensajes", status_code=201)
async def enviar_mensaje(
    id: int,
    mensaje: schemas.MensajeBody,
    usuario_actual: int = Depends(obtener_usuario_autenticado),
    db: Session = Depends(get_db)
):
    texto_limpio = mensaje.contenido.strip()
    if not texto_limpio:
        raise HTTPException(status_code=400, detail="El contenido del mensaje no puede estar vacío.")

    receptor_id_calc: Optional[int] = mensaje.receptor_id
    grupo_id_calc: Optional[int] = mensaje.grupo_id
    destinatarios: List[int] = []

    if grupo_id_calc is not None:
        # Prioridad a mensaje grupal (ck_mensajes_destino exige receptor_id = None)
        receptor_id_calc = None
        miembros_grupo = db.query(models.GrupoUsuario).filter(
            models.GrupoUsuario.grupo_id == grupo_id_calc
        ).all()
        ids_miembros = [m.usuario_id for m in miembros_grupo]
        if ids_miembros and usuario_actual not in ids_miembros:
            raise HTTPException(status_code=403, detail="Prohibido: No eres miembro activo de este grupo.")
        destinatarios = [uid for uid in ids_miembros if uid != usuario_actual]
    elif receptor_id_calc is not None:
        # Prioridad a mensaje individual directo (ck_mensajes_destino exige grupo_id = None)
        grupo_id_calc = None
        destinatarios = [receptor_id_calc]
    elif id in PARES_INDIVIDUALES:
        miembros_par = PARES_INDIVIDUALES[id]
        if usuario_actual not in miembros_par:
            raise HTTPException(status_code=403, detail="No perteneces a esta conversación individual.")
        otros = [u for u in miembros_par if u != usuario_actual]
        receptor_id_calc = otros[0] if otros else None
        grupo_id_calc = None
        destinatarios = otros
    else:
        grupo_bd = db.query(models.Grupo).filter(models.Grupo.grupo_id == id).first()
        if grupo_bd is not None:
            grupo_id_calc = grupo_bd.grupo_id
            receptor_id_calc = None
            miembros_grupo = db.query(models.GrupoUsuario).filter(
                models.GrupoUsuario.grupo_id == grupo_bd.grupo_id
            ).all()
            ids_miembros = [m.usuario_id for m in miembros_grupo]
            if ids_miembros and usuario_actual not in ids_miembros:
                raise HTTPException(status_code=403, detail="Prohibido: No eres miembro activo de este grupo.")
            destinatarios = [uid for uid in ids_miembros if uid != usuario_actual]
        else:
            receptor_id_calc = id
            grupo_id_calc = None
            destinatarios = [id]

    # Cumplir restricción ck_mensajes_no_a_si_mismo
    if receptor_id_calc is not None and receptor_id_calc == usuario_actual:
        raise HTTPException(status_code=400, detail="No puedes enviarte un mensaje individual a ti mismo.")

    entregado_en_vivo = any(manager.is_connected(uid) for uid in destinatarios)
    estado_inicial = "leido" if entregado_en_vivo else "enviado"

    nuevo_msg = models.Mensaje(
        remitente_id=usuario_actual,
        receptor_id=receptor_id_calc,
        grupo_id=grupo_id_calc,
        contenido=texto_limpio,
        enviado_en=now_bogota(),
        leido=entregado_en_vivo,
        estado=estado_inicial
    )
    db.add(nuevo_msg)
    db.commit()
    db.refresh(nuevo_msg)

    respuesta = serializar_mensaje(nuevo_msg, conv_id_ref=id)

    for uid_dest in destinatarios:
        if manager.is_connected(uid_dest):
            await manager.send_to_user(uid_dest, {"evento": "nuevo_mensaje", "data": respuesta})
        else:
            nueva_alerta = models.Notificacion(
                usuario_id=uid_dest,
                tipo="nuevo_mensaje",
                referencia_id=nuevo_msg.mensaje_id,
                texto=f"Nuevo mensaje del estudiante {usuario_actual}"[:150],
                leida=False,
                creada_en=now_bogota()
            )
            db.add(nueva_alerta)
    db.commit()

    return respuesta

# ==========================================
# 5. OBTENER MENSAJES DE UNA CONVERSACIÓN
# ==========================================
@app.get("/api/v1/conversaciones/{id}/mensajes")
async def obtener_mensajes(
    id: int,
    usuario_id: Optional[int] = Query(default=None),
    limite: int = Query(default=50, ge=1, le=100),
    antes_de: Optional[int] = Query(default=None),
    db: Session = Depends(get_db)
):
    query = db.query(models.Mensaje)

    if usuario_id is not None:
        query = query.filter(
            and_(
                models.Mensaje.grupo_id.is_(None),
                or_(
                    and_(models.Mensaje.remitente_id == id, models.Mensaje.receptor_id == usuario_id),
                    and_(models.Mensaje.remitente_id == usuario_id, models.Mensaje.receptor_id == id)
                )
            )
        )
    elif id in PARES_INDIVIDUALES:
        u1, u2 = PARES_INDIVIDUALES[id][0], PARES_INDIVIDUALES[id][1]
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
        grupo_bd = db.query(models.Grupo).filter(models.Grupo.grupo_id == id).first()
        if grupo_bd is not None:
            query = query.filter(models.Mensaje.grupo_id == id)
        else:
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
    return [serializar_mensaje(m, conv_id_ref=id) for m in mensajes]

# ==========================================
# 6. LISTAR CONVERSACIONES DE UN USUARIO
# ==========================================
@app.get("/api/v1/usuarios/{id}/conversaciones")
async def listar_conversaciones_usuario(
    id: int,
    db: Session = Depends(get_db)
):
    resultado: List[Dict[str, Any]] = []
    contactos_vistos = set()

    # 1. PRIMERO: Chats individuales recién abiertos en memoria
    for par in reversed(list(PARES_INDIVIDUALES.values())):
        if id in par:
            otro_id = par[1] if par[0] == id else par[0]
            if otro_id not in contactos_vistos:
                contactos_vistos.add(otro_id)
                u_otro = db.query(models.Usuario).filter(models.Usuario.usuario_id == otro_id).first()
                nombre_chat = f"{u_otro.nombre} {u_otro.apellido or ''}".strip() if u_otro else "Estudiante de StudyMatch"
                ult_msg = db.query(models.Mensaje).filter(
                    and_(
                        models.Mensaje.grupo_id.is_(None),
                        or_(
                            and_(models.Mensaje.remitente_id == id, models.Mensaje.receptor_id == otro_id),
                            and_(models.Mensaje.remitente_id == otro_id, models.Mensaje.receptor_id == id)
                        )
                    )
                ).order_by(models.Mensaje.mensaje_id.desc()).first()
                resultado.append({
                    "conversacion_id": otro_id,
                    "tipo": "individual",
                    "grupo_id": None,
                    "nombre": nombre_chat,
                    "ultimo_mensaje": (ult_msg.contenido if (ult_msg and ult_msg.estado != "eliminado") else "Sala abierta - envía el primer mensaje"),
                    "creada_en": fmt_fecha(ult_msg.enviado_en) if ult_msg else now_bogota_iso()
                })

    # 2. SEGUNDO: Chats individuales con historial de mensajes en Neon
    msgs_ind = db.query(models.Mensaje).filter(
        and_(
            models.Mensaje.grupo_id.is_(None),
            or_(models.Mensaje.remitente_id == id, models.Mensaje.receptor_id == id)
        )
    ).order_by(models.Mensaje.mensaje_id.desc()).limit(50).all()

    for m in msgs_ind:
        otro_id = m.receptor_id if m.remitente_id == id else m.remitente_id
        if otro_id is not None and otro_id not in contactos_vistos:
            contactos_vistos.add(otro_id)
            u_otro = db.query(models.Usuario).filter(models.Usuario.usuario_id == otro_id).first()
            nombre_chat = f"{u_otro.nombre} {u_otro.apellido or ''}".strip() if u_otro else "Estudiante de StudyMatch"
            resultado.append({
                "conversacion_id": otro_id,
                "tipo": "individual",
                "grupo_id": None,
                "nombre": nombre_chat,
                "ultimo_mensaje": None if m.estado == "eliminado" else m.contenido,
                "creada_en": fmt_fecha(m.enviado_en)
            })

    # 3. TERCERO: Conversaciones grupales en Neon (sin duplicar nombres iguales)
    nombres_grupo_vistos = set()
    membresias = db.query(models.GrupoUsuario).filter(models.GrupoUsuario.usuario_id == id).all()
    for memb in membresias:
        grp = db.query(models.Grupo).filter(models.Grupo.grupo_id == memb.grupo_id).first()
        if grp is None or grp.nombre in nombres_grupo_vistos:
            continue
        nombres_grupo_vistos.add(grp.nombre)
        ult = db.query(models.Mensaje).filter(models.Mensaje.grupo_id == grp.grupo_id).order_by(models.Mensaje.mensaje_id.desc()).first()
        resultado.append({
            "conversacion_id": grp.grupo_id,
            "tipo": "grupal",
            "grupo_id": grp.grupo_id,
            "nombre": grp.nombre,
            "ultimo_mensaje": None if (ult is None or ult.estado == "eliminado") else ult.contenido,
            "creada_en": fmt_fecha(grp.fecha_creacion)
        })

    return resultado

# ==========================================
# 7. CREAR NOTIFICACIÓN (TIEMPO REAL PARA AMBOS ESTUDIANTES DEL MATCH)
# ==========================================

@app.post("/api/v1/notificaciones", status_code=201)
async def crear_notificacion(
    evento: schemas.NotificacionMatchCreate,
    db: Session = Depends(get_db)
):
    usuario_real = db.query(models.Usuario).filter(models.Usuario.usuario_id == evento.usuario_id).first()
    if usuario_real is None:
        raise HTTPException(status_code=404, detail="El estudiante destinatario no existe en Neon.")

    u_match = db.query(models.Usuario).filter(models.Usuario.usuario_id == evento.usuario_match_id).first()
    nombre_destino = f"{usuario_real.nombre} {usuario_real.apellido or ''}".strip()
    nombre_compa = f"{u_match.nombre} {u_match.apellido or ''}".strip() if u_match else "un compañero compatible"

    # 1. Guardar notificación para el estudiante destino con el nombre real
    texto_destino = f"¡Nuevo match ({evento.porcentaje_match}%) con {nombre_compa}!"[:150]
    nueva_notif = models.Notificacion(
        usuario_id=evento.usuario_id,
        tipo="nuevo_match",
        referencia_id=evento.match_id,
        texto=texto_destino,
        leida=False,
        creada_en=now_bogota()
    )
    db.add(nueva_notif)
    db.commit()
    db.refresh(nueva_notif)

    payload_destino = {
        "notificacion_id": nueva_notif.notificacion_id,
        "usuario_id": nueva_notif.usuario_id,
        "tipo": nueva_notif.tipo,
        "referencia_id": nueva_notif.referencia_id,
        "texto": nueva_notif.texto,
        "leida": nueva_notif.leida,
        "creada_en": fmt_fecha(nueva_notif.creada_en)
    }

    if manager.is_connected(evento.usuario_id):
        await manager.send_to_user(evento.usuario_id, {"evento": "nueva_notificacion", "data": payload_destino})

    # 2. Notificación espejo en tiempo real para el compañero con el nombre real
    if evento.usuario_match_id != evento.usuario_id and u_match is not None:
        notif_espejo = models.Notificacion(
            usuario_id=evento.usuario_match_id,
            tipo="nuevo_match",
            referencia_id=evento.match_id,
            texto=f"¡Nuevo match ({evento.porcentaje_match}%) con {nombre_destino}!"[:150],
            leida=False,
            creada_en=now_bogota()
        )
        db.add(notif_espejo)
        db.commit()
        db.refresh(notif_espejo)

        payload_espejo = {
            "notificacion_id": notif_espejo.notificacion_id,
            "usuario_id": notif_espejo.usuario_id,
            "tipo": notif_espejo.tipo,
            "referencia_id": notif_espejo.referencia_id,
            "texto": notif_espejo.texto,
            "leida": notif_espejo.leida,
            "creada_en": fmt_fecha(notif_espejo.creada_en)
        }
        if manager.is_connected(evento.usuario_match_id):
            await manager.send_to_user(evento.usuario_match_id, {"evento": "nueva_notificacion", "data": payload_espejo})

    return payload_destino

# ==========================================
# 8. LISTAR NOTIFICACIONES
# ==========================================
@app.get("/api/v1/notificaciones")
async def listar_mis_notificaciones(
    usuario_actual: int = Depends(obtener_usuario_autenticado),
    db: Session = Depends(get_db)
):
    filas = db.query(models.Notificacion).filter(
        models.Notificacion.usuario_id == usuario_actual
    ).order_by(models.Notificacion.notificacion_id.desc()).all()
    return [
        {
            "notificacion_id": n.notificacion_id,
            "usuario_id": n.usuario_id,
            "tipo": n.tipo,
            "referencia_id": n.referencia_id,
            "texto": n.texto,
            "leida": n.leida,
            "creada_en": fmt_fecha(n.creada_en)
        }
        for n in filas
    ]

@app.get("/api/v1/usuarios/{id}/notificaciones")
async def listar_notificaciones_por_id(
    id: int,
    db: Session = Depends(get_db)
):
    filas = db.query(models.Notificacion).filter(
        models.Notificacion.usuario_id == id
    ).order_by(models.Notificacion.notificacion_id.desc()).all()
    return [
        {
            "notificacion_id": n.notificacion_id,
            "usuario_id": n.usuario_id,
            "tipo": n.tipo,
            "referencia_id": n.referencia_id,
            "texto": n.texto,
            "leida": n.leida,
            "creada_en": fmt_fecha(n.creada_en)
        }
        for n in filas
    ]

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
        raise HTTPException(status_code=400, detail="No se puede editar un mensaje eliminado.")

    msg.contenido = datos.contenido.strip()
    msg.estado = "editado"
    msg.editado_en = now_bogota()
    db.commit()
    db.refresh(msg)

    return serializar_mensaje(msg)

# ==========================================
# 10. BORRAR MENSAJE (SOFT DELETE CUMPLIENDO ck_mensajes_contenido)
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

    # ck_mensajes_contenido exige estrictamente: estado = 'eliminado' AND contenido IS NULL
    msg.contenido = None
    msg.estado = "eliminado"
    msg.eliminado_en = now_bogota()
    db.commit()
    db.refresh(msg)

    return serializar_mensaje(msg)

# ==========================================
# 11. SALIR DE UN GRUPO
# ==========================================
@app.delete("/api/v1/conversaciones/{id}/miembros/{usuario_id}")
async def salir_de_grupo(
    id: int,
    usuario_id: int,
    db: Session = Depends(get_db)
):
    miembro = db.query(models.GrupoUsuario).filter(
        models.GrupoUsuario.grupo_id == id,
        models.GrupoUsuario.usuario_id == usuario_id
    ).first()
    if miembro is None:
        raise HTTPException(status_code=404, detail="El usuario no es miembro activo de este grupo en Neon.")

    db.delete(miembro)
    db.commit()

    return {
        "conversacion_id": id,
        "usuario_id": usuario_id,
        "estado": "removido",
        "salio_en": now_bogota_iso()
    }