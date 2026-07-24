import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from sqlalchemy import String, Boolean, DateTime, Enum, ForeignKey, Numeric, JSON, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base

class Role(str, PyEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    OPERATIONS_MANAGER = "OPERATIONS_MANAGER"
    DUTY_OFFICER = "DUTY_OFFICER"
    MEET_AND_ASSIST_STAFF = "MEET_AND_ASSIST_STAFF"
    DRIVER = "DRIVER"
    CONCIERGE_TEAM = "CONCIERGE_TEAM"
    CUSTOMER_SUPPORT = "CUSTOMER_SUPPORT"
    CUSTOMER = "CUSTOMER"
    DISPATCHER = "DISPATCHER"
    FINANCE = "FINANCE"

class BookingStatus(str, PyEnum):
    DRAFT = "DRAFT"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

class NotificationStatus(str, PyEnum):
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    OPENED = "OPENED"
    READ = "READ"
    BYPASSED = "BYPASSED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"

class VipTier(str, PyEnum):
    VVIP = "VVIP"
    VIP = "VIP"
    CORPORATE = "CORPORATE"
    AIRLINE_CREW = "AIRLINE_CREW"
    DIPLOMATIC = "DIPLOMATIC"
    PRIVATE_CHARTER = "PRIVATE_CHARTER"
    MEDICAL_ASSISTANCE = "MEDICAL_ASSISTANCE"
    REGULAR = "REGULAR"

class CaseStatus(str, PyEnum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"

class UserAuth(Base):
    __tablename__ = "user_auth"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.CUSTOMER, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    profile = relationship("Profile", back_populates="user_auth", uselist=False)
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user_auth.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    device_id: Mapped[str] = mapped_column(String, index=True, nullable=True)
    browser: Mapped[str] = mapped_column(String, nullable=True)
    platform: Mapped[str] = mapped_column(String, nullable=True)
    ip_address: Mapped[str] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_activity: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("UserAuth", back_populates="refresh_tokens")

class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    auth_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user_auth.id", ondelete="CASCADE"), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=True)
    phone_number: Mapped[str] = mapped_column(String, nullable=True)
    avatar_url: Mapped[str] = mapped_column(String, nullable=True)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.CUSTOMER, nullable=False)
    company: Mapped[str] = mapped_column(String, nullable=True)
    vip_status: Mapped[bool] = mapped_column(Boolean, default=False)
    vip_tier: Mapped[VipTier] = mapped_column(Enum(VipTier), default=VipTier.REGULAR, nullable=False)
    passport_number: Mapped[str] = mapped_column(String, index=True, nullable=True)
    tags: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    documents_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user_auth = relationship("UserAuth", back_populates="profile")
    bookings = relationship("Booking", back_populates="profile")

class Booking(Base):
    __tablename__ = "bookings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_ref: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    passenger_name: Mapped[str] = mapped_column(String, nullable=False)
    passenger_email: Mapped[str] = mapped_column(String, index=True, nullable=False)
    passenger_phone: Mapped[str] = mapped_column(String, nullable=False)
    flight_num: Mapped[str] = mapped_column(String, nullable=False)
    origin_code: Mapped[str] = mapped_column(String, nullable=False)
    dest_code: Mapped[str] = mapped_column(String, nullable=False)
    departure_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    arrival_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    service_type: Mapped[str] = mapped_column(String, nullable=False)
    selected_services: Mapped[dict] = mapped_column(JSON, nullable=False)
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String, default="INR", nullable=False)
    status: Mapped[BookingStatus] = mapped_column(Enum(BookingStatus), default=BookingStatus.PENDING, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    profile = relationship("Profile", back_populates="bookings")

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user_auth.id", ondelete="SET NULL"), nullable=True)
    actor_email: Mapped[str] = mapped_column(String, index=True, nullable=False)
    action: Mapped[str] = mapped_column(String, index=True, nullable=False)
    resource_type: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[str] = mapped_column(String, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    ip_address: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc))

class StaffAssignment(Base):
    __tablename__ = "staff_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False)
    assigned_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user_auth.id", ondelete="SET NULL"), nullable=True)
    staff_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user_auth.id", ondelete="CASCADE"), nullable=False)
    role_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="ASSIGNED", nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class ShiftRecord(Base):
    __tablename__ = "shift_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staff_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user_auth.id", ondelete="CASCADE"), nullable=False)
    shift_name: Mapped[str] = mapped_column(String, nullable=False)
    shift_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    airport_code: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="SCHEDULED", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class AirportManagement(Base):
    __tablename__ = "airports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    city: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str] = mapped_column(String, default="IND", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    operating_hours: Mapped[str] = mapped_column(String, default="24/7", nullable=False)
    services_config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class NotificationRecord(Base):
    __tablename__ = "notification_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipient_email: Mapped[str] = mapped_column(String, index=True, nullable=True)
    recipient_phone: Mapped[str] = mapped_column(String, index=True, nullable=True)
    template_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    channel: Mapped[str] = mapped_column(String, default="ALL", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[NotificationStatus] = mapped_column(Enum(NotificationStatus), default=NotificationStatus.QUEUED, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    error_log: Mapped[str] = mapped_column(Text, nullable=True)
    message_id: Mapped[str] = mapped_column(String, nullable=True)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class CustomerCase(Base):
    __tablename__ = "customer_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_number: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String, default="GENERAL", nullable=False)
    status: Mapped[CaseStatus] = mapped_column(Enum(CaseStatus), default=CaseStatus.OPEN, nullable=False)
    assigned_to_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user_auth.id", ondelete="SET NULL"), nullable=True)
    resolution_notes: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class CustomerInteraction(Base):
    __tablename__ = "customer_interactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    actor_email: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc))

class FlightStatusRecord(Base):
    __tablename__ = "flight_status_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    flight_number: Mapped[str] = mapped_column(String, index=True, nullable=False)
    airline_code: Mapped[str] = mapped_column(String, nullable=True)
    origin_code: Mapped[str] = mapped_column(String, index=True, nullable=False)
    dest_code: Mapped[str] = mapped_column(String, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String, default="SCHEDULED", nullable=False)
    departure_gate: Mapped[str] = mapped_column(String, nullable=True)
    arrival_gate: Mapped[str] = mapped_column(String, nullable=True)
    departure_terminal: Mapped[str] = mapped_column(String, nullable=True)
    arrival_terminal: Mapped[str] = mapped_column(String, nullable=True)
    baggage_belt: Mapped[str] = mapped_column(String, nullable=True)
    checkin_counter: Mapped[str] = mapped_column(String, nullable=True)
    scheduled_departure: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    estimated_departure: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_departure: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_arrival: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_arrival: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_arrival: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ServicesConfig(Base):
    __tablename__ = "services_config"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    base_price: Mapped[float] = mapped_column(Numeric(10, 2), default=0.0)
    currency: Mapped[str] = mapped_column(String, default="INR")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    features: Mapped[dict] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Coupon(Base):
    __tablename__ = "coupons"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    discount_percent: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=True)
    times_used: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class IpRestriction(Base):
    __tablename__ = "ip_restrictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ip_address: Mapped[str] = mapped_column(String, index=True, nullable=False)
    type: Mapped[str] = mapped_column(String, default="BLOCK", nullable=False)  # BLOCK | ALLOW
    reason: Mapped[str] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class Lounge(Base):
    __tablename__ = "lounges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    airport_code: Mapped[str] = mapped_column(String, index=True, nullable=False)
    terminal: Mapped[str] = mapped_column(String, nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    amenities: Mapped[dict] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Passenger(Base):
    __tablename__ = "passengers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    gender: Mapped[str] = mapped_column(String, default="unspecified")
    date_of_birth: Mapped[str] = mapped_column(String, nullable=True)
    nationality: Mapped[str] = mapped_column(String, nullable=True)
    passport_number: Mapped[str] = mapped_column(String, index=True, nullable=True)
    passport_expiry: Mapped[str] = mapped_column(String, nullable=True)
    visa_number: Mapped[str] = mapped_column(String, nullable=True)
    visa_expiry: Mapped[str] = mapped_column(String, nullable=True)
    phone: Mapped[str] = mapped_column(String, nullable=True)
    email: Mapped[str] = mapped_column(String, nullable=True)
    special_assistance: Mapped[str] = mapped_column(Text, nullable=True)
    meal_preference: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class BookingPassenger(Base):
    __tablename__ = "booking_passengers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False)
    passenger_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("passengers.id", ondelete="CASCADE"), nullable=False)
    seat_preference: Mapped[str] = mapped_column(String, nullable=True)
    is_primary_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    remarks: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)  # stripe | razorpay
    provider_payment_id: Mapped[str] = mapped_column(String, index=True, nullable=True)
    provider_order_id: Mapped[str] = mapped_column(String, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String, default="INR", nullable=False)
    status: Mapped[str] = mapped_column(String, default="completed", nullable=False)  # pending | completed | failed | refunded
    payment_method: Mapped[str] = mapped_column(String, nullable=True)
    receipt_number: Mapped[str] = mapped_column(String, nullable=True)
    transaction_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ContactMessage(Base):
    __tablename__ = "contact_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[str] = mapped_column(String, index=True, nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=True)
    subject: Mapped[str] = mapped_column(String, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, default="new", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BrandingProfile(Base):
    __tablename__ = "branding_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_name: Mapped[str] = mapped_column(String, nullable=False, default="Shafsky Aviation")
    tagline: Mapped[str] = mapped_column(String, nullable=True)
    logo_url: Mapped[str] = mapped_column(String, nullable=True)
    primary_color: Mapped[str] = mapped_column(String, default="#5ed3ff")
    secondary_color: Mapped[str] = mapped_column(String, default="#06090f")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String, index=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    published_by: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class UserNotification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str] = mapped_column(String, nullable=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[str] = mapped_column(String, nullable=True)
    booking_ref: Mapped[str] = mapped_column(String, nullable=True)
    recipient: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    template: Mapped[str] = mapped_column(String, nullable=True)
    subject: Mapped[str] = mapped_column(String, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="sent", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SupportCase(Base):
    __tablename__ = "support_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_ref: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    customer_id: Mapped[str] = mapped_column(String, nullable=True)
    customer_email: Mapped[str] = mapped_column(String, nullable=False)
    customer_name: Mapped[str] = mapped_column(String, nullable=False)
    customer_phone: Mapped[str] = mapped_column(String, nullable=True)
    booking_id: Mapped[str] = mapped_column(String, nullable=True)
    case_type: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[str] = mapped_column(String, default="Low", nullable=False)
    status: Mapped[str] = mapped_column(String, default="OPEN", nullable=False)
    assigned_admin_id: Mapped[str] = mapped_column(String, nullable=True)
    tags: Mapped[dict] = mapped_column(JSON, default=list)
    labels: Mapped[dict] = mapped_column(JSON, default=list)
    sla_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    csat_rating: Mapped[int] = mapped_column(Integer, nullable=True)
    csat_comment: Mapped[str] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class CaseMessage(Base):
    __tablename__ = "case_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("support_cases.id", ondelete="CASCADE"), nullable=False)
    sender_id: Mapped[str] = mapped_column(String, nullable=True)
    sender_role: Mapped[str] = mapped_column(String, default="customer", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[dict] = mapped_column(JSON, default=list)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    note_category: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class CaseAuditLog(Base):
    __tablename__ = "case_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("support_cases.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SavedReply(Base):
    __tablename__ = "saved_replies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shortcut: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class BookingDocument(Base):
    __tablename__ = "booking_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    storage_path: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String, default="INR", nullable=False)
    generated_by: Mapped[str] = mapped_column(String, nullable=True)
    document_type: Mapped[str] = mapped_column(String, nullable=True)
    filename: Mapped[str] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    checksum: Mapped[str] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    whatsapp_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

