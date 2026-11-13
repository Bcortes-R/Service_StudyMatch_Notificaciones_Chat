from typing import List, Optional

from pydantic import BaseModel, Field


class ConversacionIndividualCreate(BaseModel):
    usuario_id_1: int
    usuario_id_2: int
    origen: Optional[str] = Field(default="directo")


class ConversacionGrupalCreate(BaseModel):
    grupo_id: Optional[int] = None
    nombre: str
    usuario_ids: List[int]
    materia_id: Optional[int] = 1
    creador_id: Optional[int] = None


class MensajeCreate(BaseModel):
    conversacion_id: Optional[int] = None
    remitente_id: int
    receptor_id: Optional[int] = None
    grupo_id: Optional[int] = None
    contenido: str = Field(..., max_length=150)


# Alias de compatibilidad por si algún otro módulo lo importa con otro nombre
MensajeEnviar = MensajeCreate


class NotificacionMatchCreate(BaseModel):
    match_id: Optional[int] = 101
    usuario_id: int
    usuario_match_id: int
    habilidad_id: Optional[int] = 1
    porcentaje_match: Optional[float] = 100.0