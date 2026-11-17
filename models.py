from sqlalchemy import Column, Integer, String, Text, Boolean, Date, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base
import database

Base = getattr(database, "Base", declarative_base())


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = {"extend_existing": True}

    usuario_id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(150), nullable=False)
    correo = Column(String(150), unique=True, nullable=True)


class Materia(Base):
    __tablename__ = "materias"
    __table_args__ = {"extend_existing": True}

    materia_id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(150), nullable=False)


class Grupo(Base):
    __tablename__ = "grupos"
    __table_args__ = {"extend_existing": True}

    grupo_id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(150), nullable=False)
    descripcion = Column(Text, nullable=True)
    materia_id = Column(Integer, ForeignKey("materias.materia_id"), nullable=True)
    creador_id = Column(Integer, ForeignKey("usuarios.usuario_id"), nullable=True)
    max_miembros = Column(Integer, default=10)
    estado = Column(String(20), default="activo")
    fecha_creacion = Column(Date, nullable=True)


class GrupoUsuario(Base):
    __tablename__ = "grupo_usuario"
    __table_args__ = {"extend_existing": True}

    grupo_id = Column(Integer, ForeignKey("grupos.grupo_id"), primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.usuario_id"), primary_key=True)
    rol = Column(String(20), default="miembro")
    fecha_union = Column(Date, nullable=True)


class Mensaje(Base):
    __tablename__ = "mensajes"
    __table_args__ = {"extend_existing": True}

    mensaje_id = Column(Integer, primary_key=True, index=True)
    remitente_id = Column(Integer, ForeignKey("usuarios.usuario_id"), nullable=False)
    receptor_id = Column(Integer, ForeignKey("usuarios.usuario_id"), nullable=True)
    grupo_id = Column(Integer, ForeignKey("grupos.grupo_id"), nullable=True)
    contenido = Column(String(150), nullable=False)
    enviado_en = Column(DateTime, nullable=True)
    leido = Column(Boolean, default=False)
    estado = Column(String(20), default="enviado")


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