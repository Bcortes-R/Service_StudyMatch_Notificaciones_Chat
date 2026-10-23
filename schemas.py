from pydantic import BaseModel, Field
from typing import List

class ConversacionIndividualCreate(BaseModel):
    usuario_id_1: int = Field(..., gt=0)
    usuario_id_2: int = Field(..., gt=0)
    origen: str = Field(default="match")  # "match" valida contra Mockoon; "directo" omite validación externa

class ConversacionGrupalCreate(BaseModel):
    grupo_id: int = Field(..., gt=0)
    nombre: str = Field(..., min_length=1, max_length=150)
    miembros: List[int] = Field(..., min_length=2)

class MensajeBody(BaseModel):
    contenido: str = Field(..., min_length=1, max_length=2000)

class NotificacionMatchCreate(BaseModel):
    match_id: int = Field(..., gt=0)
    usuario_id: int = Field(..., gt=0)
    usuario_match_id: int = Field(..., gt=0)
    habilidad_id: int = Field(..., gt=0)
    porcentaje_match: float = Field(..., ge=0, le=100)