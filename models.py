from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from database import Base
from datetime import datetime

class Conversacion(Base):
    __tablename__ = "conversaciones"
    
    conversacion_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tipo = Column(String, default="individual") # Puede ser "individual" o "grupal"
    creada_en = Column(DateTime, default=datetime.utcnow)

class Mensaje(Base):
    __tablename__ = "mensajes"
    
    mensaje_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    conversacion_id = Column(Integer, ForeignKey("conversaciones.conversacion_id"))
    remitente_id = Column(Integer) # Este lo inyectaremos simulando el JWT
    contenido = Column(String, nullable=True)
    enviado_en = Column(DateTime, default=datetime.utcnow)
    estado = Column(String, default="enviado") # "enviado", "editado", "eliminado"