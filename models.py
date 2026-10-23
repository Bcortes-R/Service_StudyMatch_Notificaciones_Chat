from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from database import Base
from datetime import datetime, timezone, timedelta

# Zona horaria de Bogotá (UTC-5) exigida por grupo API Gateway
BOGOTA_TZ = timezone(timedelta(hours=-5))

def now_bogota_iso() -> str:
    return datetime.now(BOGOTA_TZ).isoformat(timespec="seconds")

class Conversacion(Base):
    __tablename__ = "conversaciones"

    conversacion_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tipo = Column(String, nullable=False, default="individual")  # "individual"  "grupal"
    grupo_id = Column(Integer, nullable=True)                    # Solo si es grupal
    nombre = Column(String(150), nullable=True)                  # Nombre del grupo
    creada_en = Column(String, default=now_bogota_iso)

class MiembroConversacion(Base):
    __tablename__ = "miembros_conversacion"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    conversacion_id = Column(Integer, ForeignKey("conversaciones.conversacion_id"), nullable=False)
    usuario_id = Column(Integer, nullable=False, index=True)
    estado = Column(String, default="activo")                    # "activo"  "removido"
    salio_en = Column(String, nullable=True)

class Mensaje(Base):
    __tablename__ = "mensajes"

    mensaje_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    conversacion_id = Column(Integer, ForeignKey("conversaciones.conversacion_id"), nullable=False)
    remitente_id = Column(Integer, nullable=False)               # Compatible con emisor_id de BD
    contenido = Column(String(2000), nullable=True)              # Permite NULL para Soft Delete
    enviado_en = Column(String, default=now_bogota_iso)
    estado = Column(String, default="enviado")                   # "enviado" "entregado"  "leido"  "editado"  "eliminado"
    editado_en = Column(String, nullable=True)
    eliminado_en = Column(String, nullable=True)

class Notificacion(Base):
    __tablename__ = "notificaciones"

    notificacion_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id = Column(Integer, nullable=False, index=True)
    tipo = Column(String(50), nullable=False)                    # "nuevo_match"  "nuevo_mensaje"
    referencia_id = Column(Integer, nullable=True)               # match_id o conversacion_id
    texto = Column(String(255), nullable=False)
    leida = Column(Boolean, default=False)
    creada_en = Column(String, default=now_bogota_iso)