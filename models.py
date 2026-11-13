from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

BOGOTA_TZ = timezone(timedelta(hours=-5))

def now_bogota() -> datetime:
    return datetime.now(BOGOTA_TZ).replace(tzinfo=None)

def now_bogota_iso() -> str:
    return datetime.now(BOGOTA_TZ).isoformat(timespec="seconds")

class Usuario(Base):
    __tablename__ = "usuarios"

    usuario_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    apellido: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    correo: Mapped[str] = mapped_column(String(150), nullable=False)
    contrasena_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    carrera: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    semestre: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    foto: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    fecha_registro: Mapped[Optional[datetime]] = mapped_column(DateTime, default=now_bogota)
    correo_verificado: Mapped[Optional[bool]] = mapped_column(Boolean, default=True)
    estado: Mapped[Optional[str]] = mapped_column(String(20), default="activo")

class Materia(Base):
    __tablename__ = "materias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    materia_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String(255), nullable=False)
    carrera: Mapped[str] = mapped_column(String(255), nullable=False)
    dia: Mapped[str] = mapped_column(String(50), nullable=False)
    hora: Mapped[str] = mapped_column(String(50), nullable=False)
    salon: Mapped[str] = mapped_column(String(50), nullable=False)

class Grupo(Base):
    __tablename__ = "grupos"

    grupo_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String(150), nullable=False)
    materia_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    fecha_creacion: Mapped[Optional[datetime]] = mapped_column(DateTime, default=now_bogota)

class GrupoUsuario(Base):
    __tablename__ = "grupo_usuario"

    grupo_id: Mapped[int] = mapped_column(Integer, ForeignKey("grupos.grupo_id"), primary_key=True)
    usuario_id: Mapped[int] = mapped_column(Integer, ForeignKey("usuarios.usuario_id"), primary_key=True)
    # ck_grupo_usuario_rol solo permite 'admin' o 'usuario'
    rol: Mapped[Optional[str]] = mapped_column(String(50), default="usuario")

class Mensaje(Base):
    __tablename__ = "mensajes"

    mensaje_id: Mapped[int] = mapped_column("mensaje_id", Integer, primary_key=True, autoincrement=True)
    grupo_id: Mapped[Optional[int]] = mapped_column("grupo_id", Integer, ForeignKey("grupos.grupo_id"), nullable=True)
    remitente_id: Mapped[int] = mapped_column("emisor_id", Integer, ForeignKey("usuarios.usuario_id"), nullable=False)
    receptor_id: Mapped[Optional[int]] = mapped_column("receptor_id", Integer, ForeignKey("usuarios.usuario_id"), nullable=True)
    contenido: Mapped[Optional[str]] = mapped_column("contenido", String(150), nullable=True)
    enviado_en: Mapped[Optional[datetime]] = mapped_column("fecha_envio", DateTime, default=now_bogota)
    leido: Mapped[bool] = mapped_column("leido", Boolean, default=False)
    estado: Mapped[Optional[str]] = mapped_column("estado", String(20), default="enviado")
    editado_en: Mapped[Optional[datetime]] = mapped_column("editado_en", DateTime, nullable=True)
    eliminado_en: Mapped[Optional[datetime]] = mapped_column("eliminado_en", DateTime, nullable=True)

class Notificacion(Base):
    __tablename__ = "notificaciones"

    notificacion_id: Mapped[int] = mapped_column("notificacion_id", Integer, primary_key=True, autoincrement=True)
    usuario_id: Mapped[int] = mapped_column("usuario_id", Integer, ForeignKey("usuarios.usuario_id"), nullable=False)
    tipo: Mapped[str] = mapped_column("tipo", String(50), nullable=False)
    referencia_id: Mapped[Optional[int]] = mapped_column("referencia_id", Integer, nullable=True)
    texto: Mapped[str] = mapped_column("texto", String(150), nullable=False)
    leida: Mapped[bool] = mapped_column("leida", Boolean, default=False)
    creada_en: Mapped[Optional[datetime]] = mapped_column("creada_en", DateTime, default=now_bogota)