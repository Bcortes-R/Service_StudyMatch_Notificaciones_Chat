from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import declarative_base

import database

Base = getattr(database, "Base", declarative_base())


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = {"extend_existing": True}

    usuario_id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False)
    apellido = Column(String(100), nullable=True)
    correo = Column(String(150), unique=True, nullable=False)
    carrera = Column(String(120), nullable=True)
    semestre = Column(Integer, nullable=True)
    estado = Column(String(20), default="activo")


class Materia(Base):
    __tablename__ = "materias"
    __table_args__ = {"extend_existing": True}

    materia_id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(150), nullable=False)
    carrera = Column(String(120), nullable=True)


class Grupo(Base):
    __tablename__ = "grupos"
    __table_args__ = {"extend_existing": True}

    grupo_id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(150), nullable=False)
    materia_id = Column(Integer, ForeignKey("materias.materia_id"), nullable=True)
    fecha_creacion = Column(Date, nullable=True)


class GrupoUsuario(Base):
    __tablename__ = "grupo_usuario"
    __table_args__ = {"extend_existing": True}

    grupo_id = Column(Integer, ForeignKey("grupos.grupo_id"), primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.usuario_id"), primary_key=True)
    rol = Column(String(30), default="miembro")


class Mensaje(Base):
    __tablename__ = "mensajes"
    __table_args__ = {"extend_existing": True}

    mensaje_id = Column(Integer, primary_key=True, index=True)
    grupo_id = Column(Integer, ForeignKey("grupos.grupo_id"), nullable=True)
    # Mapea automáticamente remitente_id -> columna real 'emisor_id' en Neon
    remitente_id = Column("emisor_id", Integer, ForeignKey("usuarios.usuario_id"), nullable=False)
    receptor_id = Column(Integer, ForeignKey("usuarios.usuario_id"), nullable=True)
    contenido = Column(String(150), nullable=False)
    # Mapea automáticamente enviado_en -> columna real 'fecha_envio' en Neon
    enviado_en = Column("fecha_envio", DateTime, nullable=True)
    leido = Column(Boolean, default=False)
    estado = Column(String(20), default="enviado")
    editado_en = Column(DateTime, nullable=True)
    eliminado_en = Column(DateTime, nullable=True)


class Notificacion(Base):
    __tablename__ = "notificaciones"
    __table_args__ = {"extend_existing": True}

    notificacion_id = Column(Integer, primary_key=True, index=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.usuario_id"), nullable=False)
    tipo = Column(String(50), nullable=False)
    referencia_id = Column(Integer, nullable=True)
    texto = Column(String(150), nullable=False)
    leida = Column(Boolean, default=False)
    creada_en = Column(DateTime, nullable=True)