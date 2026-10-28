from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.exc import OperationalError
from fastapi import HTTPException

SQLALCHEMY_DATABASE_URL = "sqlite:///./studymatch_mensajeria.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    except OperationalError:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Base de datos temporalmente no disponible. Intente más tarde."
        )
    finally:
        db.close()