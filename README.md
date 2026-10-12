# StudyMatch - Servicio de Mensajería y Notificaciones 💬

Este repositorio contiene el microservicio de Mensajería y Notificaciones para el proyecto **StudyMatch** (Grupo 5). El servicio centraliza la comunicación directa entre estudiantes, administrando conversaciones individuales, chats grupales y la emisión de notificaciones en tiempo real.

## 🛠️ Stack Tecnológico

* **Lenguaje:** Python 3.x
* **Framework:** FastAPI
* **Servidor ASGI:** Uvicorn (`uvicorn[standard]`)
* **ORM:** SQLAlchemy
* **Base de Datos:** SQLite (Entorno de desarrollo local) / PostgreSQL (Producción - Grupo 8)
* **Tiempo Real:** WebSockets
* **Testing/Simulación:** Hoppscotch y Mockoon

## 🚀 Estado Actual y Avances

Hasta la fecha, el microservicio cuenta con los siguientes hitos completados:

1. **Contrato de Integración (v1.3):** Acuerdos técnicos definidos con API Gateway, Matching, Usuarios y Base de Datos. Se eliminó el acoplamiento por base de datos (JOINs) en favor de consultas REST internas.
2. **Arquitectura Base FastAPI:** Enrutamiento REST para la creación de chats y envío de mensajes estructurado bajo esquemas Pydantic.
3. **Comunicación en Tiempo Real:** Implementación de túnel bidireccional mediante WebSockets (`/api/v1/chat/ws`) con soporte para *broadcasting*.
4. **Persistencia de Datos (ORM):** Configuración de SQLAlchemy con una base de datos local temporal (`SQLite`) para permitir el desarrollo aislado sin depender del servidor de PostgreSQL en esta fase.
5. **Aislamiento de Entorno:** Configuración de entorno virtual (`venv`) y exclusión de archivos temporales/sensibles mediante `.gitignore`.

## 📂 Estructura del Proyecto

```Python
mensajeria_service/
├── main.py              # Instancia de FastAPI, rutas REST y gestor de WebSockets
├── database.py          # Configuración de SQLAlchemy y conexión a SQLite local
├── models.py            # Modelos físicos de la base de datos (ORM)
├── schemas.py           # (Próximamente) Modelos Pydantic para validación de JSON
├── requirements.txt     # Dependencias del proyecto
└── .gitignore           # Reglas de exclusión para Git

git clone <url-del-repositorio>
cd mensajeria_service
```

# ⚙️ Instrucciones de Instalación y Ejecución

Para levantar este microservicio en un entorno de desarrollo local, sigue estos pasos:

1. **Clonar el repositorio**

```Shell
git clone <url-del-repositorio>
cd mensajeria_service
```

2. **Crear y activar el entorno virtual**

```Shell
Windows:

python -m venv venv
venv\Scripts\activate
```

3. ##### **Instalar las dependencias**

```Shell
pip install -r requirements.txt
```

4. **Instalar las dependencias**

```Shell
uvicorn main:app --reload
```

El servidor estará corriendo en http://localhost:8000 .
La documentación automática de la API estará disponible en http://localhost:8000/docs .

# 🧪 Pruebas Locales (Hoppscotch)

**Crear conversación de prueba:** `POST http://localhost:8000/api/v1/conversaciones/crear_prueba`

**Enviar mensaje HTTP:** `POST http://localhost:8000/api/v1/conversaciones/1/mensajes`

**Conexión WebSocket (Chat en vivo):** Usa la pestaña *Realtime* en Hoppscotch apuntando a `ws://localhost:8000/api/v1/chat/ws`.
