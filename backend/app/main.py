
import os
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import engine
from app.routers import auth, patient, staff

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clinicq")

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:8000,http://127.0.0.1:8000"
    ).split(",")
    if origin.strip()
]

app = FastAPI(
    title="ClinicQ API",
    description="Queue Prediction and Crowd Management Engine",
    version="0.3.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


# Reject browser requests from unapproved origins
# when they attempt to change server state.
@app.middleware("http")
async def check_request_origin(request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")

        if origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse(
                status_code=403,
                content={"detail": "Request origin not allowed"}
            )

    return await call_next(request)


app.include_router(auth.router)
app.include_router(staff.router)
app.include_router(patient.router)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "clinicq-api",
        "message": "ClinicQ backend is running"
    }


@app.get("/health/db")
def database_health_check():
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            result.scalar_one()

        return {
            "status": "ok",
            "service": "clinicq-api",
            "database": "connected"
        }

    except SQLAlchemyError:
        logger.exception("Database health check failed")

        raise HTTPException(
            status_code=503,
            detail="Database connection failed"
        )
