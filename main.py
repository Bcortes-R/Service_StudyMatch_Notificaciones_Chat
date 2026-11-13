import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db

# Forzar la recarga del archivo .env
load_dotenv(override=True)

BOGOTA_TZ = timezone(timedelta(hours=-5))


def now_bogota() -> datetime:
    return datetime.now(BOGOTA_TZ).replace(tzinfo=None)


def now_bogota_iso() -> str:
    return datetime.now(BOGOTA_TZ).isoformat(timespec="seconds")


def fmt_fecha(val: Any) -> str:
    if val is None:
        return now_bogota_iso()
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=BOGOTA_TZ).isoformat(timespec="seconds")
        return val.astimezone(BOGOTA_TZ).isoformat(timespec="seconds")
    if isinstance(val, date):
        dt = datetime(val.year, val.month, val.day, tzinfo=BOGOTA_TZ)
        return dt.isoformat(timespec="seconds")
    return str(val)


def nombre_completo_usuario(u: Optional[models.Usuario], fallback_texto: str = "Estudiante de StudyMatch") -> str:
    if u is None:
        return fallback_texto
    nombre = (u.nombre or "").strip()
    apellido = (u.apellido or "").strip()
    completo = f"{nombre} {apellido}".strip()
    return completo if completo else (u.correo or fallback_texto)


app = FastAPI(
    title="StudyMatch - Servicio de Mensajería y Notificaciones",
    version="2.2-NocheNeon",
    description="Microservicio en producción sincronizado con Neon PostgreSQL y Servicio de Matching en Render."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MATCHING_SERVICE_URL = os.getenv(
    "MATCHING_SERVICE_URL",
    "https://servicio-matching.onrender.com/api/v1/matching"
).strip()
USUARIOS_SERVICE_URL = os.getenv(
    "USUARIOS_SERVICE_URL",
    "http://localhost:3001/api/v1/users"
).strip()

INTERNAL_HEADERS = {"X-Internal-Service": "mensajeria-notificaciones"}

# Registro en memoria de salas individuales recién abiertas (sin mensajes aún)
PARES_INDIVIDUALES: Dict[int, List[int]] = {}
SEQ_INDIVIDUAL: int = 1


# ==========================================
# GESTOR DE CONEXIONES WEBSOCKET EN TIEMPO REAL
# ==========================================
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, usuario_id: int, websocket: WebSocket):
        await websocket.accept()
        if usuario_id not in self.active_connections:
            self.active_connections[usuario_id] = []
        self.active_connections[usuario_id].append(websocket)

    def disconnect(self, usuario_id: int, websocket: WebSocket):
        if usuario_id in self.active_connections:
            if websocket in self.active_connections[usuario_id]:
                self.active_connections[usuario_id].remove(websocket)
            if not self.active_connections[usuario_id]:
                del self.active_connections[usuario_id]

    def is_connected(self, usuario_id: int) -> bool:
        return bool(self.active_connections.get(usuario_id))

    async def send_to_user(self, usuario_id: int, message: Dict[str, Any]):
        conexiones = list(self.active_connections.get(usuario_id, []))
        for ws in conexiones:
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(usuario_id, ws)


manager = ConnectionManager()


# ==========================================
# 0. CLIENTE WEB Y ESTADO DE SALUD
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def servir_cliente_web():
    ruta_html = Path(__file__).parent / "cliente_chat.html"
    if not ruta_html.exists():
        return HTMLResponse(
            content="<h1>Archivo cliente_chat.html no encontrado en la raíz del proyecto.</h1>",
            status_code=404
        )
    return HTMLResponse(content=ruta_html.read_text(encoding="utf-8"), status_code=200)


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "mensajeria-notificaciones",
        "matching_url": MATCHING_SERVICE_URL,
        "timestamp": now_bogota_iso()
    }


# ==========================================
# 1. DATOS INICIALES (USUARIOS Y MATERIAS CON NOMBRES REALES)
# ==========================================
@app.get("/api/v1/datos-iniciales")
async def obtener_datos_iniciales(db: Session = Depends(get_db)):
    usuarios_db = db.query(models.Usuario).order_by(models.Usuario.usuario_id.asc()).all()
    materias_db = db.query(models.Materia).order_by(models.Materia.materia_id.asc()).all()
    grupos_db = db.query(models.Grupo).order_by(models.Grupo.grupo_id.asc()).all()

    return {
        "usuarios": [
            {
                "usuario_id": u.usuario_id,
                "nombre": u.nombre,
                "apellido": u.apellido or "",
                "nombre_completo": nombre_completo_usuario(u),
                "correo": u.correo,
                "carrera": getattr(u, "carrera", None)
            }
            for u in usuarios_db
        ],
        "materias": [
            {
                "materia_id": m.materia_id,
                "nombre": m.nombre
            }
            for m in materias_db
        ],
        "grupos": [
            {
                "grupo_id": g.grupo_id,
                "nombre": g.nombre,
                "materia_id": g.materia_id
            }
            for g in grupos_db
        ]
    }


# ==========================================
# 2. CREAR CONVERSACIÓN INDIVIDUAL (DIRECTA O VALIDADA CON MATCH)
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
            detail="Selecciona un compañero distinto al estudiante que tienes activo en la sesión."
        )

    u1 = db.query(models.Usuario).filter(models.Usuario.usuario_id == datos.usuario_id_1).first()
    u2 = db.query(models.Usuario).filter(models.Usuario.usuario_id == datos.usuario_id_2).first()
    if u1 is None or u2 is None:
        raise HTTPException(
            status_code=404,
            detail="Uno o ambos estudiantes seleccionados no existen en la base de datos Neon."
        )

    nombre_u1 = nombre_completo_usuario(u1)
    nombre_u2 = nombre_completo_usuario(u2)

    if datos.origen == "match":
        match_confirmado = False

        # 1. Verificar si ya existe un match registrado entre ambos en la tabla notificaciones de Neon
        notifs_match = db.query(models.Notificacion).filter(
            models.Notificacion.usuario_id == datos.usuario_id_1,
            models.Notificacion.tipo == "nuevo_match"
        ).all()

        for n in notifs_match:
            txt = (n.texto or "").lower()
            if (
                nombre_u2.lower() in txt
                or (u2.nombre and u2.nombre.lower() in txt)
                or f"estudiante {datos.usuario_id_2}" in txt
                or f"#{datos.usuario_id_2}" in txt
            ):
                match_confirmado = True
                break

        # 2. Si no está en las notificaciones locales, consultar el Servicio de Matching en Render
        if not match_confirmado:
            try:
                async with httpx.AsyncClient(timeout=20.0, headers=INTERNAL_HEADERS, follow_redirects=True) as client:
                    url_consulta = f"{MATCHING_SERVICE_URL}/{datos.usuario_id_1}"
                    resp = await client.get(url_consulta)
                    if resp.status_code == 200:
                        data_match = resp.json()
                        coincidencias = data_match.get("coincidencias", []) if isinstance(data_match, dict) else []
                        match_remoto = next(
                            (c for c in coincidencias if c.get("usuario_match_id") == datos.usuario_id_2),
                            None
                        )
                        if match_remoto is not None:
                            match_confirmado = True
                            match_id_ref = match_remoto.get("match_id", 101)
                            porcentaje = match_remoto.get("porcentaje_match", 100.0)

                            existe_notif = db.query(models.Notificacion).filter(
                                models.Notificacion.usuario_id == datos.usuario_id_1,
                                models.Notificacion.tipo == "nuevo_match",
                                models.Notificacion.referencia_id == match_id_ref
                            ).first()

                            if existe_notif is None:
                                nueva_notif = models.Notificacion(
                                    usuario_id=datos.usuario_id_1,
                                    tipo="nuevo_match",
                                    referencia_id=match_id_ref,
                                    texto=f"¡Nuevo match ({porcentaje}%) con {nombre_u2}!"[:150],
                                    leida=False,
                                    creada_en=now_bogota()
                                )
                                db.add(nueva_notif)
                                db.commit()
                                db.refresh(nueva_notif)

                                if manager.is_connected(datos.usuario_id_1):
                                    await manager.send_to_user(datos.usuario_id_1, {
                                        "evento": "nueva_notificacion",
                                        "data": {
                                            "notificacion_id": nueva_notif.notificacion_id,
                                            "usuario_id": nueva_notif.usuario_id,
                                            "tipo": nueva_notif.tipo,
                                            "referencia_id": nueva_notif.referencia_id,
                                            "texto": nueva_notif.texto,
                                            "leida": nueva_notif.leida,
                                            "creada_en": fmt_fecha(nueva_notif.creada_en)
                                        }
                                    })
                    elif resp.status_code >= 500:
                        raise HTTPException(
                            status_code=503,
                            detail="El servicio de Matching en Render reportó un fallo interno."
                        )
            except httpx.RequestError as e:
                raise HTTPException(
                    status_code=503,
                    detail=f"Servicio de Matching temporalmente no disponible: {str(e)}"
                )

        if not match_confirmado:
            raise HTTPException(
                status_code=403,
                detail=f"No existe un match activo entre {nombre_u1} y {nombre_u2}."
            )

    conv_id = datos.usuario_id_2
    PARES_INDIVIDUALES[SEQ_INDIVIDUAL] = [datos.usuario_id_1, datos.usuario_id_2]
    SEQ_INDIVIDUAL += 1

    return {
        "conversacion_id": conv_id,
        "tipo": "individual",
        "nombre": nombre_u2,
        "miembros": [datos.usuario_id_1, datos.usuario_id_2],
        "creada_en": now_bogota_iso()
    }


# ==========================================
# 3. CREAR GRUPO DE ESTUDIO
# ==========================================
@app.post("/api/v1/conversaciones/grupo", status_code=201)
async def crear_conversacion_grupal(
    datos: schemas.ConversacionGrupalCreate,
    db: Session = Depends(get_db)
):
    lista_miembros = list(dict.fromkeys(datos.usuario_ids))
    if len(lista_miembros) < 2:
        raise HTTPException(
            status_code=400,
            detail="Un grupo de estudio requiere al menos 2 integrantes."
        )

    usuarios_existentes = db.query(models.Usuario).filter(
        models.Usuario.usuario_id.in_(lista_miembros)
    ).all()
    if len(usuarios_existentes) != len(lista_miembros):
        raise HTTPException(
            status_code=404,
            detail="Uno o más estudiantes seleccionados no existen en Neon."
        )

    materia_id = getattr(datos, "materia_id", None) or 1
    materia_db = db.query(models.Materia).filter(models.Materia.materia_id == materia_id).first()
    if materia_db is None:
        primera_materia = db.query(models.Materia).first()
        if primera_materia:
            materia_id = primera_materia.materia_id

    creador_id = getattr(datos, "creador_id", None) or lista_miembros[0]

    nuevo_grupo = models.Grupo(
        nombre=datos.nombre.strip(),
        descripcion=f"Grupo de estudio: {datos.nombre.strip()}",
        materia_id=materia_id,
        creador_id=creador_id,
        max_miembros=max(10, len(lista_miembros)),
        estado="activo",
        fecha_creacion=now_bogota().date()
    )
    db.add(nuevo_grupo)
    db.commit()
    db.refresh(nuevo_grupo)

    for uid in lista_miembros:
        rol_miembro = "admin" if uid == creador_id else "miembro"
        membresia = models.GrupoUsuario(
            grupo_id=nuevo_grupo.grupo_id,
            usuario_id=uid,
            rol=rol_miembro,
            fecha_union=now_bogota().date()
        )
        db.add(membresia)

    db.commit()

    return {
        "conversacion_id": nuevo_grupo.grupo_id,
        "grupo_id": nuevo_grupo.grupo_id,
        "tipo": "grupal",
        "nombre": nuevo_grupo.nombre,
        "miembros": lista_miembros,
        "creada_en": fmt_fecha(nuevo_grupo.fecha_creacion)
    }


# ==========================================
# 4. ENVIAR MENSAJE (CON NOTIFICACIÓN Y WEBSOCKET USANDO NOMBRES)
# ==========================================
@app.post("/api/v1/mensajes", status_code=201)
async def enviar_mensaje(
    datos: schemas.MensajeCreate,
    db: Session = Depends(get_db)
):
    contenido_limpio = (datos.contenido or "").strip()
    if not contenido_limpio:
        raise HTTPException(status_code=400, detail="El contenido del mensaje no puede estar vacío.")
    if len(contenido_limpio) > 150:
        raise HTTPException(status_code=400, detail="El mensaje supera el límite máximo de 150 caracteres.")

    u_remitente = db.query(models.Usuario).filter(models.Usuario.usuario_id == datos.remitente_id).first()
    if u_remitente is None:
        raise HTTPException(status_code=404, detail="El estudiante remitente no existe en Neon.")

    nombre_remitente = nombre_completo_usuario(u_remitente)

    es_grupal = datos.grupo_id is not None
    receptor_id = None if es_grupal else (datos.receptor_id or datos.conversacion_id)
    grupo_id = datos.grupo_id if es_grupal else None

    nuevo_msg = models.Mensaje(
        remitente_id=datos.remitente_id,
        receptor_id=receptor_id,
        grupo_id=grupo_id,
        contenido=contenido_limpio,
        enviado_en=now_bogota(),
        leido=False,
        estado="enviado"
    )
    db.add(nuevo_msg)
    db.commit()
    db.refresh(nuevo_msg)

    payload_msg = {
        "mensaje_id": nuevo_msg.mensaje_id,
        "conversacion_id": grupo_id if es_grupal else receptor_id,
        "remitente_id": nuevo_msg.remitente_id,
        "remitente_nombre": nombre_remitente,
        "receptor_id": nuevo_msg.receptor_id,
        "grupo_id": nuevo_msg.grupo_id,
        "contenido": nuevo_msg.contenido,
        "estado": nuevo_msg.estado,
        "eliminado": False,
        "enviado_en": fmt_fecha(nuevo_msg.enviado_en)
    }

    # Emitir por WebSocket a los destinatarios
    if es_grupal:
        miembros = db.query(models.GrupoUsuario).filter(models.GrupoUsuario.grupo_id == grupo_id).all()
        for m in miembros:
            if manager.is_connected(m.usuario_id):
                await manager.send_to_user(m.usuario_id, {"evento": "nuevo_mensaje", "data": payload_msg})
    else:
        for uid in {datos.remitente_id, receptor_id}:
            if uid and manager.is_connected(uid):
                await manager.send_to_user(uid, {"evento": "nuevo_mensaje", "data": payload_msg})

        # Crear notificación de nuevo mensaje con el nombre real del remitente
        if receptor_id and receptor_id != datos.remitente_id:
            notif_msg = models.Notificacion(
                usuario_id=receptor_id,
                tipo="nuevo_mensaje",
                referencia_id=nuevo_msg.mensaje_id,
                texto=f"Nuevo mensaje de {nombre_remitente}: {contenido_limpio[:80]}"[:150],
                leida=False,
                creada_en=now_bogota()
            )
            db.add(notif_msg)
            db.commit()
            db.refresh(notif_msg)

            if manager.is_connected(receptor_id):
                await manager.send_to_user(receptor_id, {
                    "evento": "nueva_notificacion",
                    "data": {
                        "notificacion_id": notif_msg.notificacion_id,
                        "usuario_id": notif_msg.usuario_id,
                        "tipo": notif_msg.tipo,
                        "referencia_id": notif_msg.referencia_id,
                        "texto": notif_msg.texto,
                        "leida": notif_msg.leida,
                        "creada_en": fmt_fecha(notif_msg.creada_en)
                    }
                })

    return payload_msg


# ==========================================
# 4B. ELIMINAR MENSAJE (BORRADO LÓGICO EN TIEMPO REAL)
# ==========================================
@app.delete("/api/v1/mensajes/{mensaje_id}")
async def eliminar_mensaje(
    mensaje_id: int,
    usuario_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db)
):
    msg = db.query(models.Mensaje).filter(models.Mensaje.mensaje_id == mensaje_id).first()
    if msg is None:
        raise HTTPException(status_code=404, detail="El mensaje no existe.")

    if usuario_id is not None and msg.remitente_id != usuario_id:
        raise HTTPException(status_code=403, detail="Solo el autor puede eliminar este mensaje.")

    msg.estado = "eliminado"
    db.commit()
    db.refresh(msg)

    evento_borrado = {
        "evento": "mensaje_eliminado",
        "data": {
            "mensaje_id": msg.mensaje_id,
            "grupo_id": msg.grupo_id,
            "remitente_id": msg.remitente_id,
            "receptor_id": msg.receptor_id,
            "estado": "eliminado"
        }
    }

    if msg.grupo_id is not None:
        miembros = db.query(models.GrupoUsuario).filter(models.GrupoUsuario.grupo_id == msg.grupo_id).all()
        for m in miembros:
            if manager.is_connected(m.usuario_id):
                await manager.send_to_user(m.usuario_id, evento_borrado)
    else:
        for uid in {msg.remitente_id, msg.receptor_id}:
            if uid and manager.is_connected(uid):
                await manager.send_to_user(uid, evento_borrado)

    return {"mensaje_id": msg.mensaje_id, "estado": "eliminado"}


# ==========================================
# 5. LISTAR MENSAJES DE UNA CONVERSACIÓN (INCLUYE NOMBRE DEL AUTOR)
# ==========================================
@app.get("/api/v1/conversaciones/{id}/mensajes")
async def listar_mensajes_conversacion(
    id: int,
    usuario_id: Optional[int] = Query(default=None),
    tipo: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db)
):
    usuarios_map = {
        u.usuario_id: nombre_completo_usuario(u)
        for u in db.query(models.Usuario).all()
    }

    if tipo == "grupal":
        es_grupo = True
    elif tipo == "individual":
        es_grupo = False
    else:
        es_grupo = db.query(models.Grupo).filter(models.Grupo.grupo_id == id).first() is not None

    if es_grupo and usuario_id is not None:
        for m in db.query(models.Mensaje).filter(
            and_(
                models.Mensaje.grupo_id.is_(None),
                or_(
                    and_(models.Mensaje.remitente_id == usuario_id, models.Mensaje.receptor_id == id),
                    and_(models.Mensaje.remitente_id == id, models.Mensaje.receptor_id == usuario_id)
                )
            )
        ).limit(1):
            es_grupo = False

    if es_grupo:
        mensajes_db = (
            db.query(models.Mensaje)
            .filter(models.Mensaje.grupo_id == id)
            .order_by(models.Mensaje.mensaje_id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )
    else:
        if usuario_id is not None:
            condicion = and_(
                models.Mensaje.grupo_id.is_(None),
                or_(
                    and_(models.Mensaje.remitente_id == usuario_id, models.Mensaje.receptor_id == id),
                    and_(models.Mensaje.remitente_id == id, models.Mensaje.receptor_id == usuario_id)
                )
            )
        else:
            condicion = and_(
                models.Mensaje.grupo_id.is_(None),
                or_(models.Mensaje.remitente_id == id, models.Mensaje.receptor_id == id)
            )

        mensajes_db = (
            db.query(models.Mensaje)
            .filter(condicion)
            .order_by(models.Mensaje.mensaje_id.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    return [
        {
            "mensaje_id": m.mensaje_id,
            "remitente_id": m.remitente_id,
            "remitente_nombre": usuarios_map.get(m.remitente_id, "Estudiante"),
            "receptor_id": m.receptor_id,
            "grupo_id": m.grupo_id,
            "contenido": None if m.estado == "eliminado" else m.contenido,
            "estado": m.estado,
            "eliminado": m.estado == "eliminado",
            "enviado_en": fmt_fecha(m.enviado_en)
        }
        for m in mensajes_db
    ]


# ==========================================
# 6. LISTAR CONVERSACIONES DE UN USUARIO (SOLO NOMBRES REALES, SIN IDs)
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
                nombre_chat = nombre_completo_usuario(u_otro)
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
                    "ultimo_mensaje": (
                        ult_msg.contenido
                        if (ult_msg and ult_msg.estado != "eliminado")
                        else "Sala abierta - envía el primer mensaje"
                    ),
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
            nombre_chat = nombre_completo_usuario(u_otro)
            resultado.append({
                "conversacion_id": otro_id,
                "tipo": "individual",
                "grupo_id": None,
                "nombre": nombre_chat,
                "ultimo_mensaje": None if m.estado == "eliminado" else m.contenido,
                "creada_en": fmt_fecha(m.enviado_en)
            })

    # 3. TERCERO: Conversaciones grupales en Neon (sin repetir grupos con el mismo nombre)
    nombres_grupo_vistos = set()
    membresias = db.query(models.GrupoUsuario).filter(models.GrupoUsuario.usuario_id == id).all()
    for memb in membresias:
        grp = db.query(models.Grupo).filter(models.Grupo.grupo_id == memb.grupo_id).first()
        if grp is None or grp.nombre in nombres_grupo_vistos:
            continue
        nombres_grupo_vistos.add(grp.nombre)
        ult = db.query(models.Mensaje).filter(
            models.Mensaje.grupo_id == grp.grupo_id
        ).order_by(models.Mensaje.mensaje_id.desc()).first()

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
# 7. CREAR Y LISTAR NOTIFICACIONES (CON NOMBRES REALES Y TIEMPO REAL)
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
    nombre_destino = nombre_completo_usuario(usuario_real)
    nombre_compa = nombre_completo_usuario(u_match, "un compañero compatible")

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

    # 2. Guardar y enviar notificación espejo en tiempo real para el otro estudiante del match
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


@app.get("/api/v1/notificaciones")
async def listar_notificaciones(
    usuario_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db)
):
    usuarios_map = {
        u.usuario_id: nombre_completo_usuario(u)
        for u in db.query(models.Usuario).all()
    }

    query = db.query(models.Notificacion)
    if usuario_id is not None:
        query = query.filter(models.Notificacion.usuario_id == usuario_id)

    notifs = query.order_by(models.Notificacion.notificacion_id.desc()).limit(40).all()

    resultado = []
    for n in notifs:
        texto_formateado = n.texto or ""
        # Reemplazar textos históricos que tenían "estudiante X" por el nombre real de Neon
        for uid, nombre_real in usuarios_map.items():
            texto_formateado = texto_formateado.replace(f"estudiante {uid}", nombre_real)
            texto_formateado = texto_formateado.replace(f"Estudiante #{uid}", nombre_real)

        resultado.append({
            "notificacion_id": n.notificacion_id,
            "usuario_id": n.usuario_id,
            "tipo": n.tipo,
            "referencia_id": n.referencia_id,
            "texto": texto_formateado,
            "leida": n.leida,
            "creada_en": fmt_fecha(n.creada_en)
        })

    return resultado


# ==========================================
# 8. CANAL WEBSOCKET PARA CHAT Y ALERTAS EN VIVO
# ==========================================
@app.websocket("/api/v1/chat/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    usuario_id: int = Query(...)
):
    await manager.connect(usuario_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("ping"):
                await websocket.send_json({"evento": "pong", "usuario_id": usuario_id})
    except WebSocketDisconnect:
        manager.disconnect(usuario_id, websocket)
    except Exception:
        manager.disconnect(usuario_id, websocket)