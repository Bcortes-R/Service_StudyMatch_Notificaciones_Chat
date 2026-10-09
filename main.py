from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
from typing import List 
from datetime import datetime


app = FastAPI(title= 'StudyMatch - Servicio de Mensajeria y Notificaciones')

# ==========================================
#            ESQUEMA DE VALIDACIÓN
# ==========================================

class ConverssacionCreate(BaseModel):
    id_usuario1: int
    id_usuario2: int
    origen: str
    
class MensajeCreate(BaseModel):
# Se omite remitente_id ya que se obtiene del token de autenticación
    contenido: str
    
class NotificacionMatch(BaseModel):
# Se aplicala estructura exigida por el grupo 1 (Matching)
    match_id: int
    usuario_id: int
    usuario_match_id: int
    habilidad_id: int
    
