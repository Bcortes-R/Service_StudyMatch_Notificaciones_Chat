from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List
from datetime import datetime

app = FastAPI(title="StudyMatch - Mensajería")

# ==========================================
# 1. ESQUEMAS Y SIMULACIÓN DE BASE DE DATOS
# ==========================================
class MensajeCreate(BaseModel):
    contenido: str

# Listas que simulan PostgreSQL en memoria
tabla_mensajes = []

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

# ==========================================
# 2. RUTAS REST (HTTP)
# ==========================================
@app.post("/api/v1/conversaciones/{id}/mensajes", status_code=201)
async def enviar_mensaje(id: int, mensaje: MensajeCreate):
    nuevo_mensaje = {
        "mensaje_id": len(tabla_mensajes) + 1,
        "conversacion_id": id,
        "contenido": mensaje.contenido,
        "enviado_en": datetime.now().isoformat(),
        "estado": "enviado"
    }
    tabla_mensajes.append(nuevo_mensaje) # Guardamos en memoria
    return nuevo_mensaje

@app.get("/api/v1/conversaciones/{id}/mensajes")
async def obtener_mensajes(id: int):
    # Filtramos los mensajes de esta conversación específica
    return [msg for msg in tabla_mensajes if msg["conversacion_id"] == id]

# ==========================================
# 3. RUTA WEBSOCKET (TIEMPO REAL)
# ==========================================
@app.websocket("/api/v1/chat/ws")
async def chat_websocket(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await manager.broadcast(f"Mensaje en vivo recibido: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)