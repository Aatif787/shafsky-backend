"""
Pydantic Schemas for Website Contact Management System.
Supports exactly six canonical services with two slots each.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
import re


CANONICAL_SERVICES = [
    ("meet_greet_lounge", "Meet & Greet & Lounge"),
    ("transport", "Transport"),
    ("hotel", "Hotel"),
    ("private_charter", "Private Charter"),
    ("ticketing", "Ticketing"),
    ("contact", "Contact"),
]

CANONICAL_SERVICE_KEYS = [k for k, _ in CANONICAL_SERVICES]
CANONICAL_SERVICE_DICT = dict(CANONICAL_SERVICES)


class ContactSlotInput(BaseModel):
    slot_index: int = Field(..., ge=1, le=2, description="Slot index (must be 1 or 2)")
    country_code: Optional[str] = Field(default="", description="Calling country code (e.g. +91)")
    phone_number: Optional[str] = Field(default="", description="Subscriber phone number (e.g. 9599087959)")
    is_active: Optional[bool] = Field(default=True, description="Whether slot is active")

    @field_validator("country_code")
    @classmethod
    def validate_country_code(cls, v: Optional[str]) -> str:
        if not v:
            return ""
        val = v.strip()
        if not val:
            return ""
        if not re.match(r"^\+[1-9]\d{0,3}$", val):
            raise ValueError(f"Invalid country code '{val}'. Must start with '+' followed by 1 to 4 digits (e.g. +91, +1).")
        return val

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, v: Optional[str]) -> str:
        if not v:
            return ""
        val = v.strip()
        if not val:
            return ""
        # Strip common formatting characters
        cleaned = re.sub(r"[\s\-\(\)\.]", "", val)
        if not re.match(r"^\d{5,15}$", cleaned):
            raise ValueError(f"Invalid phone number '{val}'. Must contain between 5 and 15 digits.")
        return cleaned


class ContactSlotItem(BaseModel):
    slot_index: int
    country_code: str = ""
    phone_number: str = ""
    full_phone_number: str = ""
    is_active: bool = True


class ServiceContactConfig(BaseModel):
    service_key: str
    service_name: str
    slots: List[ContactSlotItem] = Field(default_factory=list)


class ServiceContactUpdateItem(BaseModel):
    service_key: str
    slots: List[ContactSlotInput] = Field(..., min_length=1, max_length=2)

    @field_validator("service_key")
    @classmethod
    def validate_service_key(cls, v: str) -> str:
        key = v.strip().lower()
        if key not in CANONICAL_SERVICE_KEYS:
            raise ValueError(f"Invalid service_key '{v}'. Must be one of {CANONICAL_SERVICE_KEYS}.")
        return key


class ContactSettingsUpdateRequest(BaseModel):
    services: List[ServiceContactUpdateItem] = Field(..., min_length=1)


class PublicContactSlot(BaseModel):
    country_code: str
    phone_number: str
    full_phone_number: str


class PublicServiceContact(BaseModel):
    service_name: str
    contacts: List[PublicContactSlot] = Field(default_factory=list)


class PublicContactSettingsResponse(BaseModel):
    success: bool = True
    data: Dict[str, PublicServiceContact]


class AdminContactSettingsResponse(BaseModel):
    success: bool = True
    data: List[ServiceContactConfig]
