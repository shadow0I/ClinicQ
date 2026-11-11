
import re
from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class StaffLoginRequest(BaseModel):
    clinic_code: str = Field(min_length=4, max_length=20)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("clinic_code")
    @classmethod
    def validate_clinic_code(cls, value):
        value = value.strip().upper()

        if not re.fullmatch(r"[A-Z0-9]{4,20}", value):
            raise ValueError("Invalid clinic code format")

        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        value = value.strip().lower()

        if not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            value
        ):
            raise ValueError("Invalid email format")

        return value

class StaffTokenCreate(BaseModel):
    doctor_id: UUID | None = None

    patient_name: str = Field(min_length=2, max_length=100)
    age: int = Field(ge=0, le=120)
    sex: str = Field(
        pattern=r"^(Male|Female|Other|Prefer not to say)$"
    )
    mobile_number: str = Field(
        pattern=r"^[6-9]\d{9}$"
    )
    visit_reason: str = Field(
        pattern=r"^(General check-up|Follow-up check-up|Fever|Cold / cough|Other)$"
    )

class PatientQRAccessRequest(BaseModel):
    qr_token: str = Field(min_length=32, max_length=128)
class PatientAccessRequest(BaseModel):
    clinic_code: str = Field(min_length=4, max_length=20)
    token_date: date
    token_number: int = Field(gt=0, le=999999)
    access_code: str = Field(min_length=20, max_length=64)

    @field_validator("clinic_code")
    @classmethod
    def validate_clinic_code(cls, value):
        value = value.strip().upper()

        if not re.fullmatch(r"[A-Z0-9]{4,20}", value):
            raise ValueError("Invalid clinic code format")

        return value

    @field_validator("access_code")
    @classmethod
    def validate_access_code(cls, value):
        value = value.strip()

        if not value or any(char.isspace() for char in value):
            raise ValueError("Invalid patient access code")

        return value