# Estas listas en memoria actúan como tus tablas de PostgreSQL
tabla_conversaciones = []
tabla_mensajes = []

# En tu router de mensajes (routers/conversaciones.py):
@app.post("/api/v1/conversaciones/{id}/mensajes", status_code=201)
async def enviar_mensaje(id: int, mensaje: MensajeCreate):
    nuevo_mensaje = {
        "mensaje_id": len(tabla_mensajes) + 1,
        "conversacion_id": id,
        "contenido": mensaje.contenido,
        "enviado_en": datetime.now().isoformat(),
        "estado": "enviado"
    }
    # "Insertamos" en la base de datos simulada
    tabla_mensajes.append(nuevo_mensaje)
    
    # Aquí también podrías llamar a manager.broadcast() para el WebSocket
    
    return nuevo_mensaje

@app.get("/api/v1/conversaciones/{id}/mensajes")
async def obtener_mensajes(id: int):
    # "Consultamos" (filtramos) la base de datos simulada
    mensajes_del_chat = [msg for msg in tabla_mensajes if msg["conversacion_id"] == id]
    return mensajes_del_chat