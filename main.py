from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from datetime import datetime

# Importamos la base de datos y los modelos
from database import engine, get_db
import models

# ¡Esto crea el archivo físico .db y las tablas si no existen!
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="StudyMatch - Mensajería (Con SQLite)")

# ==========================================
# 1. ESQUEMAS DE VALIDACIÓN (PYDANTIC)
# ==========================================
class MensajeCreate(BaseModel):
    contenido: str

# ==========================================
# 2. WEBSOCKETS (TIEMPO REAL)
# ==========================================
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
# 3. RUTAS REST (AHORA CON BASE DE DATOS REAL)
# ==========================================

# Ruta temporal para crear una conversación de prueba rápido
@app.post("/api/v1/conversaciones/crear_prueba", status_code=201)
async def crear_conversacion_prueba(db: Session = Depends(get_db)):
    nueva_conv = models.Conversacion(tipo="individual")
    db.add(nueva_conv)
    db.commit()
    db.refresh(nueva_conv)
    return nueva_conv

@app.post("/api/v1/conversaciones/{id}/mensajes", status_code=201)
async def enviar_mensaje(id: int, mensaje: MensajeCreate, db: Session = Depends(get_db)):
    # Simulación de identidad (como si el Gateway nos pasara el JWT)
    remitente_id_simulado = 25
    
    nuevo_mensaje = models.Mensaje(
        conversacion_id=id,
        remitente_id=remitente_id_simulado,
        contenido=mensaje.contenido
    )
    
    # 1. Guardar en SQLite
    db.add(nuevo_mensaje)
    db.commit()
    db.refresh(nuevo_mensaje)
    
    # 2. Podríamos emitir el WebSocket aquí en el futuro
    
    return nuevo_mensaje

@app.get("/api/v1/conversaciones/{id}/mensajes")
async def obtener_mensajes(id: int, db: Session = Depends(get_db)):
    # Hacemos un SELECT * FROM mensajes WHERE conversacion_id = id
    mensajes = db.query(models.Mensaje).filter(models.Mensaje.conversacion_id == id).all()
    return mensajes

# ==========================================
# 4. RUTA WEBSOCKET
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