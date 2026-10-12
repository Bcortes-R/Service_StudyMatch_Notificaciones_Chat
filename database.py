from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Se apunta a una base de datos local (Un Archivo SQLite) para pruebas 
# Actualizar la URL de conexion a la base de datos real según sea necesario

SQLALCHEMY_DATABASE_URL = "sqlite:///./studymatch_mensajeria.db"  


# Configuración de la base de datos
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}  # Solo para SQLite
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Dependencia para abrir y cerrar sesiones de base de datos

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


