from typing import Optional
from sqlalchemy import Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from database import Base
from datetime import datetime, timezone, timedelta

BOGOTA_TZ = timezone(timedelta(hours=-5))

def now_bogota_iso() -> str:
    return datetime.now(BOGOTA_TZ).isoformat(timespec="seconds")

class Conversacion(Base):
    __tablename__ = "conversaciones"

    conversacion_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False, default="individual")
    grupo_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    nombre: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    creada_en: Mapped[str] = mapped_column(String(35), default=now_bogota_iso)

class MiembroConversacion(Base):
    __tablename__ = "miembros_conversacion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    conversacion_id: Mapped[int] = mapped_column(Integer, ForeignKey("conversaciones.conversacion_id"), nullable=False)
    usuario_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    estado: Mapped[str] = mapped_column(String(20), default="activo")
    salio_en: Mapped[Optional[str]] = mapped_column(String(35), nullable=True)

class Mensaje(Base):
    __tablename__ = "mensajes"

    mensaje_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    conversacion_id: Mapped[int] = mapped_column(Integer, ForeignKey("conversaciones.conversacion_id"), nullable=False)
    remitente_id: Mapped[int] = mapped_column(Integer, nullable=False)
    contenido: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    enviado_en: Mapped[str] = mapped_column(String(35), default=now_bogota_iso)
    estado: Mapped[str] = mapped_column(String(20), default="enviado")
    editado_en: Mapped[Optional[str]] = mapped_column(String(35), nullable=True)
    eliminado_en: Mapped[Optional[str]] = mapped_column(String(35), nullable=True)

class Notificacion(Base):
    __tablename__ = "notificaciones"

    notificacion_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True, autoincrement=True)
    usuario_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(50), nullable=False)
    referencia_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    texto: Mapped[str] = mapped_column(String(255), nullable=False)
    leida: Mapped[bool] = mapped_column(Boolean, default=False)
    creada_en: Mapped[str] = mapped_column(String(35), default=now_bogota_iso)