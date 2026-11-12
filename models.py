from typing import Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy import Integer, String, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from database import Base

# Zona horaria de Bogotá (UTC-5) exigida por el API Gateway (Grupo 2)
BOGOTA_TZ = timezone(timedelta(hours=-5))

def now_bogota() -> datetime:
    # Retorna datetime sin tzinfo para compatibilidad directa con TIMESTAMP de PostgreSQL
    return datetime.now(BOGOTA_TZ).replace(tzinfo=None)

def now_bogota_iso() -> str:
    return datetime.now(BOGOTA_TZ).isoformat(timespec="seconds")

class Mensaje(Base):
    """
    Mapeo directo a la tabla 'mensajes' ya existente en Neon (PostgreSQL).
    No requiere crear tablas nuevas ni agregar columnas extra.
    """
    __tablename__ = "mensajes"

    # NOTA: Si en Neon la llave primaria se llama "id" en lugar de "mensaje_id",
    # solo cambia "mensaje_id" por "id" entre las comillas de la siguiente línea:
    mensaje_id: Mapped[int] = mapped_column("mensaje_id", Integer, primary_key=True, autoincrement=True)
    remitente_id: Mapped[int] = mapped_column("emisor_id", Integer, nullable=False)
    receptor_id: Mapped[Optional[int]] = mapped_column("receptor_id", Integer, nullable=True)
    grupo_id: Mapped[Optional[int]] = mapped_column("grupo_id", Integer, nullable=True)
    contenido: Mapped[str] = mapped_column("contenido", String(150), nullable=False)
    enviado_en: Mapped[datetime] = mapped_column("fecha_envio", DateTime, default=now_bogota)
    leido: Mapped[bool] = mapped_column("leido", Boolean, default=False)