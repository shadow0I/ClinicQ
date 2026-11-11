
from fastapi import Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.security import hash_session_token

SESSION_COOKIE_NAME = "clinicq_session"


def get_current_staff(
    request: Request,
    db: Session = Depends(get_db)
):
    session_token = request.cookies.get(
        SESSION_COOKIE_NAME
    )

    if not session_token:
        raise HTTPException(
            status_code=401,
            detail="Staff authentication required"
        )

    token_hash = hash_session_token(session_token)

    result = db.execute(
        text("""
            SELECT
                s.id AS staff_id,
                s.clinic_id,
                s.email,
                s.role,
                c.name AS clinic_name
            FROM public.staff_sessions ss
            JOIN public.staff_users s
                ON s.id = ss.staff_user_id
               AND s.clinic_id = ss.clinic_id
            JOIN public.clinics c
                ON c.id = ss.clinic_id
            WHERE ss.session_token_hash = :token_hash
              AND ss.revoked_at IS NULL
              AND ss.expires_at > NOW()
              AND s.is_active = TRUE
              AND c.is_active = TRUE
            LIMIT 1
        """),
        {"token_hash": token_hash}
    ).mappings().one_or_none()

    if result is None:
        raise HTTPException(
            status_code=401,
            detail="Session expired or invalid"
        )

    return {
        "staff_id": result["staff_id"],
        "clinic_id": result["clinic_id"],
        "email": result["email"],
        "role": result["role"],
        "clinic_name": result["clinic_name"]
    }
