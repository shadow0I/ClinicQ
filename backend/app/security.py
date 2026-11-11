
import hashlib
import secrets

from pwdlib import PasswordHash

password_hasher = PasswordHash.recommended()

DUMMY_PASSWORD_HASH = password_hasher.hash(
    "This-is-not-a-real-ClinicQ-password-123!"
)


def verify_staff_password(
    plain_password: str,
    stored_hash: str
) -> bool:
    try:
        return password_hasher.verify(
            plain_password,
            stored_hash
        )
    except Exception:
        return False


def create_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def create_patient_access_code() -> str:
    return secrets.token_urlsafe(18)


def hash_patient_access_code(code: str) -> str:
    return hashlib.sha256(
        code.encode("utf-8")
    ).hexdigest()


def create_patient_qr_token() -> str:
    return secrets.token_urlsafe(32)


def hash_patient_qr_token(token: str) -> str:
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()