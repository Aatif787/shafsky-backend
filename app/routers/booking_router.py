import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.database import get_db
from app.schemas.booking import (
    BookingCreate,
    BookingApiResponse,
    BookingStatusUpdate
)
from app.schemas.enquiry import (
    ServiceEnquiryCreate,
    ServiceEnquiryApiResponse,
    ServiceEnquiryResponseData,
)
from app.services.booking_service import BookingService
from app.security.dependencies import (
    get_optional_user,
    get_required_user,
    get_required_admin,
    get_required_recycle_admin,
    get_required_super_admin,
)
from app.services.booking_recycle_service import BookingRecycleService

router = APIRouter(prefix="/api/bookings", tags=["Booking Engine"])


@router.post("/enquiries", response_model=ServiceEnquiryApiResponse, status_code=201)
@router.post("/enquiries/", response_model=ServiceEnquiryApiResponse, status_code=201, include_in_schema=False)
async def create_service_enquiry(
    payload: ServiceEnquiryCreate,
    db: Session = Depends(get_db),
):
    """
    Quote-only enquiries for hotel, transport, medical, travel, and cargo.
    Persists a PENDING booking (₹0) for the admin Bookings desk — no payment.
    Private charter must use /api/v1/charter/requests (Charter Desk).
    """
    if payload.service_category == "Private Charter":
        raise HTTPException(
            status_code=400,
            detail="Private Charter enquiries must be submitted via /api/v1/charter/requests.",
        )

    booking = BookingService.create_service_enquiry(
        db,
        passenger_name=payload.passenger_name,
        passenger_email=str(payload.passenger_email),
        passenger_phone=payload.passenger_phone,
        service_category=payload.service_category,
        service_type=payload.service_type,
        origin=payload.origin,
        destination=payload.destination,
        service_date=payload.service_date,
        notes=payload.notes,
        details=payload.details or {},
    )

    return ServiceEnquiryApiResponse(
        success=True,
        message="Enquiry received. Our desk will contact you shortly with a quotation.",
        data=ServiceEnquiryResponseData(
            id=str(booking.id),
            booking_ref=booking.booking_ref,
            passenger_name=booking.passenger_name,
            service_category=booking.service_category,
            service_type=booking.service_type,
            status=booking.status.value if hasattr(booking.status, "value") else str(booking.status),
            created_at=booking.created_at.isoformat() if booking.created_at else None,
        ),
    )


@router.post("", response_model=BookingApiResponse, status_code=201)
@router.post("/", response_model=BookingApiResponse, status_code=201)
async def create_booking(
    payload: BookingCreate,
    db: Session = Depends(get_db),
    user_context: Optional[Dict[str, Any]] = Depends(get_optional_user)
):
    profile_id = None
    if user_context:
        raw_id = user_context.get("user_id") or user_context.get("userId")
        if raw_id:
            try:
                import uuid
                profile_id = uuid.UUID(str(raw_id))
            except Exception:
                pass

    booking = BookingService.create_booking(db, payload, profile_id=profile_id)
    
    # Securely initiate payment (creates Razorpay Order server-side)
    from app.services.payment_service import PaymentService
    from app.schemas.payment import PaymentInitiateRequest, PaymentMethod
    
    init_request = PaymentInitiateRequest(
        entity_type="AIRPORT_BOOKING",
        entity_id=str(booking.booking_ref),
        customer_name=booking.passenger_name or "Valued Guest",
        customer_email=booking.passenger_email or "guest@shafsky.com",
        amount=float(booking.total_amount),
        currency=booking.currency or "INR",
        payment_method=PaymentMethod.CREDIT_CARD,
        customer_id=str(profile_id) if profile_id else None
    )
    
    try:
        transaction = PaymentService.initiate_payment(db, init_request)
        db.commit()
    except Exception as e:
        logger.exception("Payment initiation failed after booking %s was created", booking.booking_ref)
        from app.providers.razorpay_provider import razorpay_provider
        booking_dict = BookingService.format_booking_dict(booking)
        booking_dict["razorpay_order_id"] = None
        booking_dict["razorpay_key_id"] = razorpay_provider.key_id
        booking_dict["razorpay_amount_paise"] = int(round(float(booking.total_amount or 0) * 100))
        booking_dict["payment_init_failed"] = True
        return BookingApiResponse(
            success=True,
            data=booking_dict,
            error=f"Booking created but payment could not be started. Please retry payment. ({str(e)})",
        )
    
    from app.providers.razorpay_provider import razorpay_provider
    booking_dict = BookingService.format_booking_dict(booking)
    booking_dict["razorpay_order_id"] = transaction.gateway_payment_id
    booking_dict["razorpay_key_id"] = razorpay_provider.key_id
    raw_intent = transaction.gateway_response if isinstance(transaction.gateway_response, dict) else {}
    booking_dict["razorpay_amount_paise"] = int(
        raw_intent.get("amount") or round(float(booking.total_amount or 0) * 100)
    )

    return BookingApiResponse(
        success=True,
        data=booking_dict
    )


@router.get("/{identifier}/status", response_model=BookingApiResponse)
async def get_booking_status(
    identifier: str,
    db: Session = Depends(get_db)
):
    """
    Public, lightweight polling endpoint for booking and payment confirmation status.
    """
    booking = BookingService.get_booking_by_ref_or_id(db, identifier)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found.")

    from app.models.schema import BookingStatus
    from app.models.payment import PaymentTransaction, PaymentStatus
    from sqlalchemy import select, or_

    tx = db.scalar(
        select(PaymentTransaction).where(
            or_(
                PaymentTransaction.transaction_ref == booking.booking_ref,
                PaymentTransaction.entity_id == booking.booking_ref
            )
        )
    )

    is_paid = booking.status == BookingStatus.CONFIRMED or (tx and tx.status == PaymentStatus.SUCCESSFUL)

    return BookingApiResponse(
        success=True,
        data={
            "bookingRef": booking.booking_ref,
            "status": booking.status.value if hasattr(booking.status, "value") else str(booking.status),
            "paymentStatus": "PAID" if is_paid else (tx.status.value if tx and hasattr(tx.status, "value") else "PENDING"),
            "totalAmount": float(booking.total_amount),
            "currency": booking.currency,
            # Intentionally omit passenger PII on this public polling endpoint (C4 remnant).
            "serviceType": booking.service_type,
            "createdAt": booking.created_at.isoformat() if booking.created_at else None
        }
    )


@router.get("/my-bookings", response_model=BookingApiResponse)
async def get_my_bookings(
    db: Session = Depends(get_db),
    user_context: Dict[str, Any] = Depends(get_required_user)
):
    email = user_context.get("sub") or user_context.get("email") or ""
    bookings = BookingService.get_user_bookings(db, email=email)
    formatted = [BookingService.format_booking_dict(b) for b in bookings]
    return BookingApiResponse(
        success=True,
        data=formatted
    )

@router.get("/admin/list", response_model=BookingApiResponse)
@router.get("/admin/all", response_model=BookingApiResponse)
async def admin_list_bookings(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    service_category: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    bookings, total = BookingService.admin_list_bookings(
        db,
        status=status,
        search=search,
        service_category=service_category,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    formatted = [BookingService.format_booking_dict(b) for b in bookings]
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    return BookingApiResponse(
        success=True,
        data={
            "items": formatted,
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
        },
    )

@router.get("/admin/bin", response_model=BookingApiResponse)
async def admin_list_recycle_bin(
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_recycle_admin),
):
    bookings, total = BookingRecycleService.list_bin(
        db, search=search, page=page, page_size=page_size
    )
    is_super = admin_context.get("role") == "SUPER_ADMIN"
    formatted = [
        BookingRecycleService.format_bin_item(b, viewer_is_super_admin=is_super)
        for b in bookings
    ]
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    return BookingApiResponse(
        success=True,
        data={
            "items": formatted,
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
        },
    )

@router.get("/admin/deletion-log", response_model=BookingApiResponse)
async def admin_deletion_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    _super_admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    rows, total = BookingRecycleService.list_deletion_log(db, page=page, page_size=page_size)
    formatted = [BookingRecycleService.format_deletion_log(row) for row in rows]
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    return BookingApiResponse(
        success=True,
        data={
            "items": formatted,
            "total": total,
            "page": page,
            "pageSize": page_size,
            "totalPages": total_pages,
        },
    )

@router.post("/admin/{identifier}/recycle", response_model=BookingApiResponse)
async def admin_recycle_booking(
    identifier: str,
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_recycle_admin),
):
    booking = BookingRecycleService.recycle_booking(db, identifier, admin_context)
    is_super = admin_context.get("role") == "SUPER_ADMIN"
    return BookingApiResponse(
        success=True,
        data=BookingRecycleService.format_bin_item(booking, viewer_is_super_admin=is_super),
    )

@router.post("/admin/{identifier}/restore", response_model=BookingApiResponse)
async def admin_restore_booking(
    identifier: str,
    db: Session = Depends(get_db),
    admin_context: Dict[str, Any] = Depends(get_required_recycle_admin),
):
    booking = BookingRecycleService.restore_booking(db, identifier, admin_context)
    return BookingApiResponse(
        success=True,
        data=BookingService.format_booking_dict(booking),
    )

@router.delete("/admin/{identifier}/purge", response_model=BookingApiResponse)
async def admin_purge_booking(
    identifier: str,
    db: Session = Depends(get_db),
    super_admin: Dict[str, Any] = Depends(get_required_super_admin),
):
    result = BookingRecycleService.purge_booking(db, identifier, super_admin)
    return BookingApiResponse(success=True, data=result)

@router.get("/{identifier}", response_model=BookingApiResponse)
async def get_booking_details(
    identifier: str,
    db: Session = Depends(get_db),
    user_context: Dict[str, Any] = Depends(get_required_user)
):
    booking = BookingService.get_booking_by_ref_or_id(db, identifier)
    role = user_context.get("role")
    email = (user_context.get("sub") or user_context.get("email") or "").lower()
    user_id = str(user_context.get("user_id") or user_context.get("userId") or "")
    is_staff = role in (
        "SUPER_ADMIN", "ADMIN", "OPERATIONS_MANAGER", "DUTY_OFFICER",
        "DISPATCHER", "CONCIERGE_TEAM", "CUSTOMER_SUPPORT",
    )
    owns = False
    if email and (booking.passenger_email or "").lower() == email:
        owns = True
    if user_id and booking.user_id and str(booking.user_id) == user_id:
        owns = True
    if not is_staff and not owns:
        raise HTTPException(status_code=403, detail="Access denied.")
    return BookingApiResponse(
        success=True,
        data=BookingService.format_booking_dict(booking)
    )

@router.patch("/{identifier}/cancel", response_model=BookingApiResponse)
async def cancel_booking(
    identifier: str,
    version: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    user_context: Dict[str, Any] = Depends(get_required_user)
):
    email = user_context.get("sub") or user_context.get("email") or ""
    is_admin = user_context.get("role") in ["ADMIN", "SUPER_ADMIN", "DISPATCHER"]
    
    updated_booking = BookingService.cancel_booking(
        db,
        identifier,
        requester_email=email,
        is_admin=is_admin,
        expected_version=version
    )
    return BookingApiResponse(
        success=True,
        data=BookingService.format_booking_dict(updated_booking)
    )

@router.patch("/admin/{identifier}/status", response_model=BookingApiResponse)
async def admin_update_booking_status(
    identifier: str,
    payload: BookingStatusUpdate,
    db: Session = Depends(get_db),
    _admin_context: Dict[str, Any] = Depends(get_required_admin)
):
    updated_booking = BookingService.admin_update_status(
        db,
        identifier,
        new_status_str=payload.status,
        expected_version=payload.version
    )
    return BookingApiResponse(
        success=True,
        data=BookingService.format_booking_dict(updated_booking)
    )

@router.post("/estimate-price", response_model=BookingApiResponse)
async def estimate_booking_price(
    payload: Dict[str, Any],
    db: Session = Depends(get_db)
):
    package_id = str(payload.get("package_id", payload.get("service_type", "silver"))).lower()
    airport_code = str(payload.get("airport_code", payload.get("origin_code", "DEL"))).upper()
    journey_type = str(payload.get("journey_type", "DEPARTURE")).upper()
    pax_adults = max(1, int(payload.get("pax_adults", 1)))

    total_calculated = BookingService.calculate_authoritative_price(
        db=db,
        airport_code=airport_code,
        service_tier_or_slug=package_id,
        journey_type=journey_type,
        flight_type="DOMESTIC",
        pax_count=pax_adults
    )

    base_price = round(total_calculated / pax_adults, 2)
    subtotal = total_calculated
    taxes = 0
    total_amount = subtotal

    return BookingApiResponse(
        success=True,
        data={
            "base_price": base_price,
            "passengers_total": subtotal,
            "add_ons_total": 0,
            "subtotal": subtotal,
            "taxes": taxes,
            "total_amount": total_amount,
            "currency": "INR"
        }
    )
