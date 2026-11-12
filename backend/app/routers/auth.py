
import os

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import StaffLoginRequest
from app.dependencies import get_current_staff, SESSION_COOKIE_NAME
from app.security import (
    verify_staff_password,
    create_session_token,
    hash_session_token,
    DUMMY_PASSWORD_HASH
)

router = APIRouter(
    prefix="/api/auth",
    tags=["Staff Authentication"]
)

SESSION_DURATION = timedelta(hours=8)

COOKIE_SECURE = (
    os.getenv("COOKIE_SECURE", "false").lower() == "true"
)

COOKIE_SAMESITE = "none" if COOKIE_SECURE else "lax"

@router.post("/login")
def staff_login(
    payload: StaffLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    # Find staff account using clinic code and email.
    staff = db.execute(
        text("""
            SELECT
                s.id AS staff_id,
                s.clinic_id,
                s.email,
                s.password_hash,
                s.role,
                s.is_active,
                c.name AS clinic_name,
                c.is_active AS clinic_active
            FROM public.staff_users s
            JOIN public.clinics c
                ON c.id = s.clinic_id
            WHERE LOWER(c.clinic_code) = :clinic_code
              AND LOWER(s.email) = :email
            LIMIT 1
        """),
        {
            "clinic_code": payload.clinic_code.lower(),
            "email": payload.email
        }
    ).mappings().one_or_none()

    # Use a dummy password verification for unknown users.
    if staff is None:
        verify_staff_password(
            payload.password,
            DUMMY_PASSWORD_HASH
        )

        raise HTTPException(
            status_code=401,
            detail="Invalid clinic code, email or password"
        )

    if not verify_staff_password(
        payload.password,
        staff["password_hash"]
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid clinic code, email or password"
        )

    if not staff["is_active"] or not staff["clinic_active"]:
        raise HTTPException(
            status_code=401,
            detail="Invalid clinic code, email or password"
        )

    # Generate a private session token.
    raw_token = create_session_token()
    token_hash = hash_session_token(raw_token)

    expires_at = (
        datetime.now(timezone.utc) + SESSION_DURATION
    )

    try:
        # Save only the hash of the session token.
        db.execute(
            text("""
                INSERT INTO public.staff_sessions (
                    clinic_id,
                    staff_user_id,
                    session_token_hash,
                    expires_at
                )
                VALUES (
                    :clinic_id,
                    :staff_user_id,
                    :token_hash,
                    :expires_at
                )
            """),
            {
                "clinic_id": staff["clinic_id"],
                "staff_user_id": staff["staff_id"],
                "token_hash": token_hash,
                "expires_at": expires_at
            }
        )

        db.commit()

    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Unable to create staff session"
        )

    # Set the raw token in an HttpOnly cookie.
    response.set_cookie(
    key=SESSION_COOKIE_NAME,
    value=raw_token,
    httponly=True,
    secure=COOKIE_SECURE,
    samesite=COOKIE_SAMESITE,
    max_age=int(SESSION_DURATION.total_seconds()),
    path="/"
)

    return {
        "status": "ok",
        "message": "Staff login successful",
        "staff": {
            "staff_id": staff["staff_id"],
            "clinic_id": staff["clinic_id"],
            "clinic_name": staff["clinic_name"],
            "email": staff["email"],
            "role": staff["role"]
        }
    }


@router.get("/me")
def current_staff(
    staff=Depends(get_current_staff)
):
    return {
        "status": "ok",
        "staff": staff
    }


@router.post("/logout")
def staff_logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    raw_token = request.cookies.get(
        SESSION_COOKIE_NAME
    )

    if raw_token:
        token_hash = hash_session_token(raw_token)

        try:
            db.execute(
                text("""
                    UPDATE public.staff_sessions
                    SET revoked_at = NOW()
                    WHERE session_token_hash = :token_hash
                      AND revoked_at IS NULL
                """),
                {"token_hash": token_hash}
            )

            db.commit()

        except Exception:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Unable to terminate staff session"
            )

    response.delete_cookie(
    key=SESSION_COOKIE_NAME,
    path="/",
    httponly=True,
    secure=COOKIE_SECURE,
    samesite=COOKIE_SAMESITE
)

    return {
        "status": "ok",
        "message": "Staff logged out successfully"
    }
