
import re
import sys
from getpass import getpass
from pathlib import Path

from pwdlib import PasswordHash
from sqlalchemy import text

# Make the backend folder importable.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.db import engine


def main():
    print("\n===== CLINICQ INITIAL SETUP =====\n")

    clinic_name = input("Enter clinic name: ").strip()
    clinic_code = input("Create clinic code: ").strip().upper()
    doctor_name = input("Enter doctor display name: ").strip()
    email = input("Create staff login email: ").strip().lower()

    if not clinic_name or not doctor_name:
        raise ValueError("Clinic and doctor names are required.")

    if not re.fullmatch(r"[A-Z0-9]{4,20}", clinic_code):
        raise ValueError(
            "Clinic code must contain 4-20 letters or numbers."
        )

    if "@" not in email or " " in email:
        raise ValueError("Enter a valid email address.")

    password = getpass("Create staff password (minimum 12 characters): ")
    confirm_password = getpass("Confirm staff password: ")

    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters.")

    if password != confirm_password:
        raise ValueError("Passwords do not match.")

    # Hash the password using Argon2.
    password_hash = PasswordHash.recommended().hash(password)

    with engine.begin() as connection:

        # Find or create the clinic.
        clinic_id = connection.execute(
            text("""
                SELECT id
                FROM public.clinics
                WHERE LOWER(clinic_code) = LOWER(:code)
            """),
            {"code": clinic_code}
        ).scalar_one_or_none()

        if clinic_id is None:
            clinic_id = connection.execute(
                text("""
                    INSERT INTO public.clinics
                    (name, clinic_code)
                    VALUES (:name, :code)
                    RETURNING id
                """),
                {
                    "name": clinic_name,
                    "code": clinic_code
                }
            ).scalar_one()

            print("\nClinic created.")
        else:
            print("\nExisting clinic found. Reusing it.")

        # Find or create the doctor.
        doctor_id = connection.execute(
            text("""
                SELECT id
                FROM public.doctors
                WHERE clinic_id = :clinic_id
                  AND display_name = :name
                  AND is_active = TRUE
            """),
            {
                "clinic_id": clinic_id,
                "name": doctor_name
            }
        ).scalar_one_or_none()

        if doctor_id is None:
            doctor_id = connection.execute(
                text("""
                    INSERT INTO public.doctors
                    (clinic_id, display_name)
                    VALUES (:clinic_id, :name)
                    RETURNING id
                """),
                {
                    "clinic_id": clinic_id,
                    "name": doctor_name
                }
            ).scalar_one()

            print("Doctor created.")
        else:
            print("Existing doctor found. Reusing it.")

        # Avoid accidentally replacing an existing staff password.
        existing_staff = connection.execute(
            text("""
                SELECT id
                FROM public.staff_users
                WHERE clinic_id = :clinic_id
                  AND LOWER(email) = LOWER(:email)
            """),
            {
                "clinic_id": clinic_id,
                "email": email
            }
        ).scalar_one_or_none()

        if existing_staff is not None:
            raise ValueError(
                "This staff account already exists. "
                "No password has been changed."
            )

        # Create the initial clinic owner.
        staff_id = connection.execute(
            text("""
                INSERT INTO public.staff_users
                (clinic_id, email, password_hash, role)
                VALUES (:clinic_id, :email, :password_hash, 'OWNER')
                RETURNING id
            """),
            {
                "clinic_id": clinic_id,
                "email": email,
                "password_hash": password_hash
            }
        ).scalar_one()

    print("\n===== CLINICQ SETUP SUCCESSFUL =====")
    print("Clinic ID:", clinic_id)
    print("Clinic code:", clinic_code)
    print("Doctor ID:", doctor_id)
    print("Staff ID:", staff_id)
    print("Staff email:", email)
    print("Staff role: OWNER")
    print("\nPassword stored as an Argon2 hash.")
    print("No plaintext password was stored.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("\nSETUP FAILED")
        print("Error:", str(error))
        sys.exit(1)
    finally:
        engine.dispose()
