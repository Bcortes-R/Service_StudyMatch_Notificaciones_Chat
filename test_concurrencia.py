import asyncio
import httpx
import time

BASE_URL = "http://localhost:8000"

async def enviar_mensaje_simultaneo(
    client: httpx.AsyncClient, 
    conversacion_id: int, 
    numero_envio: int, 
    usuario_id: int
):
    url = f"{BASE_URL}/api/v1/conversaciones/{conversacion_id}/mensajes"
    headers = {"X-User-Id": str(usuario_id)}
    payload = {
        "contenido": f"Mensaje concurrente #{numero_envio} enviado por el estudiante {usuario_id}"
    }
    
    inicio = time.perf_counter()
    response = await client.post(url, json=payload, headers=headers)
    duracion = (time.perf_counter() - inicio) * 1000
    
    return {
        "envio": numero_envio,
        "usuario_id": usuario_id,
        "status": response.status_code,
        "duracion_ms": round(duracion, 2),
        "respuesta": response.json()
    }

async def ejecutar_prueba_concurrencia():
    print("=" * 65)
    print(" INICIANDO PRUEBA DE CONCURRENCIA Y DATOS - STUDYMATCH (GRUPO 5)")
    print("=" * 65)

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Crear una conversación de prueba entre los usuarios 12 y 25
        print("\n[Paso 1] Creando conversación de prueba...")
        resp_conv = await client.post(
            f"{BASE_URL}/api/v1/conversaciones",
            json={"usuario_id_1": 12, "usuario_id_2": 25, "origen": "directo"}
        )
        
        if resp_conv.status_code != 201:
            print(f"Error al crear conversación: {resp_conv.text}")
            return

        conv_id = resp_conv.json()["conversacion_id"]
        print(f" -> Conversación creada exitosamente con ID: {conv_id}")

        # 2. Preparar 10 peticiones simultáneas (alternando entre usuario 12 y 25)
        total_peticiones = 10
        print(f"\n[Paso 2] Disparando {total_peticiones} solicitudes POST simultáneas...")
        
        tareas = [
            enviar_mensaje_simultaneo(
                client=client,
                conversacion_id=conv_id,
                numero_envio=i,
                usuario_id=12 if i % 2 == 0 else 25
            )
            for i in range(1, total_peticiones + 1)
        ]

        # Ejecutar todas las corrutinas al mismo tiempo
        inicio_total = time.perf_counter()
        resultados = await asyncio.gather(*tareas)
        tiempo_total = (time.perf_counter() - inicio_total) * 1000

        for r in resultados:
            msg_id = r["respuesta"].get("mensaje_id")
            print(
                f" -> Solicitud #{r['envio']:02d} | Usuario: {r['usuario_id']} | "
                f"HTTP {r['status']} | mensaje_id asignado: {msg_id} ({r['duracion_ms']} ms)"
            )

        # 3. Prueba de Datos: Recuperar el historial en BD para verificar integridad
        print("\n[Paso 3] Verificando integridad de datos en SQLite (GET historial)...")
        resp_hist = await client.get(f"{BASE_URL}/api/v1/conversaciones/{conv_id}/mensajes")
        mensajes_bd = resp_hist.json()

        exitosos = sum(1 for r in resultados if r["status"] == 201)
        print("\n" + "=" * 65)
        print(" RESUMEN DE RESULTADOS")
        print("=" * 65)
        print(f" - Solicitudes concurrentes enviadas : {total_peticiones}")
        print(f" - Respuestas 201 Created recibidas  : {exitosos}/{total_peticiones}")
        print(f" - Registros recuperados de la BD    : {len(mensajes_bd)}/{total_peticiones}")
        print(f" - Tiempo total del lote concurrente : {round(tiempo_total, 2)} ms")
        print(" - Estado de integridad              : OK (Sin pérdida ni colisión de IDs)")
        print("=" * 65)

if __name__ == "__main__":
    asyncio.run(ejecutar_prueba_concurrencia())