"""Travel Services WhatsApp flows: menu, Visa Assistance and Flight Ticketing enquiries.

The bot must offer exactly two Travel Services options and collect the same fields as
the website enquiry forms, then persist the enquiry through
``BookingService.create_service_enquiry`` (the ``POST /api/bookings/enquiries`` path).
These tests also lock in BACK / CANCEL / HI / session expiry behaviour and the
duplicate-submission guard.
"""

import uuid
from unittest.mock import MagicMock

import pytest

from app.main import app  # noqa: F401
from app.database import Base, engine, SessionLocal
from app.models.schema import Booking, BookingStatus
from app.integrations.whatsapp.client import WhatsAppClient
from app.integrations.whatsapp import travel_enquiries as te
from app.integrations.whatsapp.service import WhatsAppBookingStateMachine

M = WhatsAppBookingStateMachine

FUTURE_DATE = "25/12/2026"
RETURN_DATE = "05/01/2027"
TEST_EMAIL = "aariz@shafsky-mail.com"


def _unique_email() -> str:
    """Each test submits its own enquiry, so keep the address unique per run."""
    return f"aariz+{uuid.uuid4().hex[:8]}@shafsky-mail.com"


@pytest.fixture(autouse=True)
def mock_whatsapp_network(monkeypatch):
    """No real Meta calls; every send succeeds."""
    try:
        Base.metadata.create_all(bind=engine)
    except Exception:
        pass
    for name in (
        "send_message",
        "send_text_message",
        "send_interactive_buttons",
        "send_interactive_list",
    ):
        monkeypatch.setattr(
            WhatsAppClient,
            name,
            MagicMock(return_value={"success": True, "message_id": f"wamid.{name}"}),
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _new_conversation(db):
    phone = f"91{uuid.uuid4().int % 10**10:010d}"
    conv, _created = M.get_or_create_conversation(db, phone)
    conv.current_state = "START"
    db.commit()
    return conv, phone


def _say(db, phone, text, input_id=None):
    return M.process_incoming_event(db, phone, text, input_id=input_id)


def _last_text():
    return WhatsAppClient.send_text_message.call_args.args[1]


def _last_buttons():
    return WhatsAppClient.send_interactive_buttons.call_args.kwargs


def _open_travel_menu(db, phone):
    _say(db, phone, "Hi")
    _say(db, phone, "2")


def _visa_to_review(db, phone, destination="United Arab Emirates", email=None):
    """Drive the Visa Assistance form to the review prompt (1 adult, fixed date)."""
    _open_travel_menu(db, phone)
    _say(db, phone, "1")                       # Visa Assistance
    _say(db, phone, destination)               # destination country
    _say(db, phone, "India")                   # passport issuing country
    _say(db, phone, "Skip")                    # country of residence (optional)
    _say(db, phone, "1")                       # myself
    _say(db, phone, "1")                       # holiday & tourism
    _say(db, phone, "1")                       # fixed date
    _say(db, phone, FUTURE_DATE)               # travel date
    _say(db, phone, "1")                       # 1 adult
    _say(db, phone, "Aariz")                   # traveller 1 first name
    _say(db, phone, "Khan")                    # last name
    _say(db, phone, "A1234567")                # passport number
    _say(db, phone, "Skip")                    # passport expiry (optional)
    _say(db, phone, "Skip")                    # date of birth (optional)
    _say(db, phone, "Aariz Khan")              # lead contact name
    _say(db, phone, email or _unique_email())   # email
    _say(db, phone, "Same")                    # phone
    _say(db, phone, "None")                    # consular notes


def _ticketing_to_review(db, phone, return_trip=False, from_airport="DEL", to_airport="BOM", email=None):
    """Drive Flight Ticketing to review (1 adult, domestic)."""
    _open_travel_menu(db, phone)
    _say(db, phone, "2")                                   # Flight Ticketing
    _say(db, phone, "2" if return_trip else "1")           # journey type
    _say(db, phone, from_airport)
    _say(db, phone, to_airport)
    _say(db, phone, FUTURE_DATE)
    if return_trip:
        _say(db, phone, RETURN_DATE)
    _say(db, phone, "1")                                   # cabin class
    _say(db, phone, "1")                                   # 1 adult
    _say(db, phone, "Skip")                                # airline preference
    _say(db, phone, "Aariz")                               # passenger 1 first name
    _say(db, phone, "Khan")
    _say(db, phone, "1")                                   # Male
    _say(db, phone, "01/01/1990")                          # DOB
    _say(db, phone, "Indian")                              # nationality
    _say(db, phone, "Aariz Khan")                          # lead contact
    _say(db, phone, email or _unique_email())
    _say(db, phone, "Same")


# ---------------------------------------------------------------------------
# 1-2. Menu
# ---------------------------------------------------------------------------


def test_travel_menu_offers_only_visa_and_flight_ticketing():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _say(db, phone, "Hi")
        # Main menu still has all four categories, with an updated Travel description.
        rows = WhatsAppClient.send_interactive_list.call_args.kwargs["sections"][0]["rows"]
        assert [r["id"] for r in rows] == [
            "cat_airport",
            "cat_travel",
            "cat_charter",
            "cat_hotel_transport",
        ]
        travel_row = next(r for r in rows if r["id"] == "cat_travel")
        assert travel_row["description"] == "Visa assistance and flight ticketing"
        assert "insurance" not in travel_row["description"].lower()

        _say(db, phone, "2")
        db.refresh(conv)
        assert conv.current_state == "TRAVEL_SERVICE_SELECTION"

        call = _last_buttons()
        assert [b["title"] for b in call["buttons"]] == ["Visa Assistance", "Flight Ticketing"]
        body = call["body_text"]
        for removed in ("Travel Insurance", "Hotel Booking", "VIP Escort"):
            assert removed not in body
        assert "1" in body and "2" in body
    finally:
        db.close()


def test_removed_travel_options_are_not_selectable():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _open_travel_menu(db, phone)

        for attempt in ("Travel Insurance", "Hotel Booking", "VIP Escort", "3", "4"):
            res = _say(db, phone, attempt)
            db.refresh(conv)
            assert res["status"] == "invalid_travel_service", attempt
            assert conv.current_state == "TRAVEL_SERVICE_SELECTION", attempt
            assert "Visa Assistance" in _last_text()
            assert "Flight Ticketing" in _last_text()
            assert "Insurance" not in _last_text()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. Visa Assistance
# ---------------------------------------------------------------------------


def test_visa_field_set_matches_website_form():
    answers = {
        "visa_behalf": "self",
        "visa_date_mode": "fixed",
        "visa_travellers": {"adults": 1, "children": 0, "infants": 0},
    }
    keys = [step.key for step in te.plan(te.VISA, answers)]
    assert keys == [
        "visa_destination",
        "visa_passport",
        "visa_residence",
        "visa_behalf",
        "visa_purpose",
        "visa_date_mode",
        "visa_travel_date",
        "visa_travellers",
        "visa_applicant_1_first_name",
        "visa_applicant_1_last_name",
        "visa_applicant_1_passport_number",
        "visa_applicant_1_passport_expiry",
        "visa_applicant_1_date_of_birth",
        "visa_contact_name",
        "visa_contact_email",
        "visa_contact_phone",
        "visa_notes",
    ]
    corporate = dict(answers, visa_behalf="corporate")
    assert "visa_company" in [step.key for step in te.plan(te.VISA, corporate)]
    tentative = dict(answers, visa_date_mode="tentative")
    keys = [step.key for step in te.plan(te.VISA, tentative)]
    assert "visa_tentative_month" in keys and "visa_travel_date" not in keys


def test_visa_enquiry_end_to_end_creates_website_style_enquiry():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _visa_to_review(db, phone)

        db.refresh(conv)
        assert conv.current_state == "TRAVEL_ENQUIRY"
        review = _last_text()
        assert "VISA ASSISTANCE" in review
        assert "United Arab Emirates" in review
        assert "A1234567" in review
        assert "Submit Enquiry" in [b["title"] for b in _last_buttons()["buttons"]]

        res = _say(db, phone, "1", input_id="btn_enquiry_confirm")
        db.refresh(conv)

        assert res["status"] == "travel_enquiry_registered"
        assert res["success"] is True
        assert conv.current_state == "AWAITING_QUOTE"
        assert conv.payment_status == "QUOTE_REQUESTED"
        assert conv.razorpay_payment_url is None
        assert conv.booking_ref.startswith("SHF-")

        booking = db.query(Booking).filter_by(booking_ref=conv.booking_ref).one()
        assert booking.service_category == "Travel Support"
        assert booking.service_type == "Visa Assistance"
        assert booking.status == BookingStatus.PENDING
        assert float(booking.total_amount) == 0.0
        assert booking.origin_code == "India"
        assert booking.dest_code == "United Arab Emirates"
        assert booking.service_options["destination_country"] == "United Arab Emirates"
        details = booking.metadata_json["details"]
        assert details["channel"] == "whatsapp"
        assert details["behalf_of"] == "self"
        assert details["travel_purpose"] == "tourism"
        assert details["applicants"][0]["passport_number"] == "A1234567"
        assert booking.metadata_json["source"] == "whatsapp"
        # Same enquiry notes shape the website sends (special_requests).
        assert booking.notes.startswith("Behalf: SELF | Purpose: tourism | Applicants: Aariz Khan")
        assert booking.notes.endswith("| Notes: ")  # empty optional notes, same as the website textarea

        confirmation = _last_text()
        assert conv.booking_ref in confirmation
        assert "Visa Assistance" in confirmation
        assert "Razorpay" not in confirmation
    finally:
        db.close()


def test_visa_rejects_invalid_answers_and_past_dates():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _open_travel_menu(db, phone)
        _say(db, phone, "1")

        _say(db, phone, "12345")           # digits are not a country
        assert "country name in letters" in _last_text()
        _say(db, phone, "United Arab Emirates")
        _say(db, phone, "India")
        _say(db, phone, "Skip")
        _say(db, phone, "1")
        _say(db, phone, "1")
        _say(db, phone, "1")               # fixed date

        _say(db, phone, "01/01/2020")      # past date
        assert "already passed" in _last_text()
        db.refresh(conv)
        assert conv.current_state == "TRAVEL_ENQUIRY"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4-6. Flight Ticketing
# ---------------------------------------------------------------------------


def test_ticketing_field_set_matches_website_form():
    domestic = {
        "tkt_trip_type": "one_way",
        "tkt_from": "DEL",
        "tkt_to": "BOM",
        "tkt_depart_date": "2026-12-25",
        "tkt_travellers": {"adults": 1, "children": 0, "infants": 0},
    }
    keys = [step.key for step in te.plan(te.TICKETING, domestic)]
    assert "tkt_return_date" not in keys
    assert keys[:4] == ["tkt_trip_type", "tkt_from", "tkt_to", "tkt_depart_date"]
    assert "tkt_cabin" in keys and "tkt_alliance" in keys
    for field in ("first_name", "last_name", "gender", "date_of_birth", "nationality"):
        assert f"tkt_pax_1_{field}" in keys
    for field in ("passport_number", "passport_expiry", "passport_country"):
        assert f"tkt_pax_1_{field}" not in keys

    international = dict(domestic, tkt_to="LHR", tkt_trip_type="round_trip")
    intl_keys = [step.key for step in te.plan(te.TICKETING, international)]
    assert "tkt_return_date" in intl_keys
    for field in ("passport_number", "passport_expiry", "passport_country"):
        assert f"tkt_pax_1_{field}" in intl_keys

    assert te.detect_itinerary_type("DEL", "BOM") == "DOMESTIC"
    assert te.detect_itinerary_type("DEL", "LHR") == "INTERNATIONAL"
    assert te.detect_itinerary_type("Delhi", "Dubai") == "INTERNATIONAL"


def test_one_way_ticketing_enquiry_has_no_return_date():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _ticketing_to_review(db, phone, return_trip=False)

        review = _last_text()
        assert "One-way" in review
        assert "Return" not in review

        res = _say(db, phone, "Submit", input_id="btn_enquiry_confirm")
        db.refresh(conv)
        assert res["status"] == "travel_enquiry_registered"

        booking = db.query(Booking).filter_by(booking_ref=conv.booking_ref).one()
        assert booking.service_type == "Flight Ticketing"
        assert booking.service_category == "Travel Support"
        assert booking.origin_code == "DEL"
        assert booking.dest_code == "BOM"
        assert booking.notes.startswith("Alliance: Any Alliance")
        details = booking.metadata_json["details"]
        assert details["trip_type"] == "one_way"
        assert details["return_date"] is None
        assert details["itinerary_type"] == "DOMESTIC"
        assert details["pax_adults"] == 1
        assert details["passengers"][0] == {
            "first_name": "Aariz",
            "last_name": "Khan",
            "gender": "Male",
            "date_of_birth": "1990-01-01",
            "nationality": "Indian",
            "passport_number": "",
            "passport_expiry": "",
            "passport_issuing_country": "",
        }
    finally:
        db.close()


def test_return_ticketing_validates_return_date_and_keeps_it():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _open_travel_menu(db, phone)
        _say(db, phone, "2")                      # Flight Ticketing
        _say(db, phone, "2")                      # return journey
        _say(db, phone, "DEL")
        _say(db, phone, "BOM")
        _say(db, phone, FUTURE_DATE)              # departure

        _say(db, phone, "20/12/2026")             # before departure
        assert "cannot be earlier than your departure" in _last_text()
        db.refresh(conv)
        assert conv.current_state == "TRAVEL_ENQUIRY"
        state = conv.whatsapp_state_json["travel_enquiry"]
        assert "tkt_return_date" not in state["answers"]

        _say(db, phone, RETURN_DATE)              # valid return date
        assert "cabin class" in _last_text().lower()

        _say(db, phone, "1")                      # cabin
        _say(db, phone, "1")                      # 1 adult
        _say(db, phone, "Skip")                   # alliance
        _say(db, phone, "Aariz")
        _say(db, phone, "Khan")
        _say(db, phone, "1")
        _say(db, phone, "01/01/1990")
        _say(db, phone, "Indian")
        _say(db, phone, "Aariz Khan")
        _say(db, phone, _unique_email())
        _say(db, phone, "Same")

        review = _last_text()
        assert "Return" in review
        assert "05/01/2027" in review

        _say(db, phone, "1", input_id="btn_enquiry_confirm")
        db.refresh(conv)

        booking = db.query(Booking).filter_by(booking_ref=conv.booking_ref).one()
        details = booking.metadata_json["details"]
        assert details["trip_type"] == "round_trip"
        assert details["return_date"] == "2027-01-05"
        assert booking.notes.startswith("Alliance: Any Alliance")
    finally:
        db.close()


def test_international_ticketing_collects_passport_details():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _open_travel_menu(db, phone)
        _say(db, phone, "2")
        _say(db, phone, "1")                      # one-way
        _say(db, phone, "DEL")
        _say(db, phone, "LHR")                    # international
        _say(db, phone, FUTURE_DATE)
        _say(db, phone, "1")
        _say(db, phone, "1")
        _say(db, phone, "Skip")
        _say(db, phone, "Aariz")
        _say(db, phone, "Khan")
        _say(db, phone, "1")
        _say(db, phone, "01/01/1990")
        _say(db, phone, "Indian")

        assert "passport number" in _last_text().lower()
        _say(db, phone, "A1234567")
        assert "passport expiry" in _last_text().lower()
        _say(db, phone, "01/01/2032")
        assert "passport issuing country" in _last_text().lower()
        _say(db, phone, "India")

        _say(db, phone, "Aariz Khan")
        _say(db, phone, _unique_email())
        _say(db, phone, "Same")
        _say(db, phone, "1", input_id="btn_enquiry_confirm")

        db.refresh(conv)
        booking = db.query(Booking).filter_by(booking_ref=conv.booking_ref).one()
        passenger = booking.metadata_json["details"]["passengers"][0]
        assert passenger["passport_number"] == "A1234567"
        assert passenger["passport_expiry"] == "2032-01-01"
        assert passenger["passport_issuing_country"] == "India"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 8. BACK / CANCEL / HI / session expiry / duplicates
# ---------------------------------------------------------------------------


def test_back_returns_to_previous_field_and_overwrites_answer():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _open_travel_menu(db, phone)
        _say(db, phone, "1")
        _say(db, phone, "United Arab Emirates")

        _say(db, phone, "BACK")
        db.refresh(conv)
        assert conv.current_state == "TRAVEL_ENQUIRY"
        assert "country are you applying to visit" in _last_text()

        _say(db, phone, "Qatar")
        db.refresh(conv)
        answers = conv.whatsapp_state_json["travel_enquiry"]["answers"]
        assert answers["visa_destination"] == "Qatar"
        assert "passport" in _last_text().lower()

        # BACK from the first field leaves the enquiry and clears its answers.
        _say(db, phone, "BACK")                    # back to destination
        _say(db, phone, "BACK")                    # back to the menu
        db.refresh(conv)
        assert conv.current_state == "TRAVEL_SERVICE_SELECTION"
        assert "travel_enquiry" not in (conv.whatsapp_state_json or {})
    finally:
        db.close()


def test_cancel_and_hi_clear_enquiry_state():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _open_travel_menu(db, phone)
        _say(db, phone, "1")
        _say(db, phone, "Qatar")

        res = _say(db, phone, "CANCEL")
        db.refresh(conv)
        assert res["status"] == "cancelled"
        assert conv.current_state == "CANCELLED"
        assert conv.whatsapp_state_json is None

        _say(db, phone, "Hi")
        db.refresh(conv)
        assert conv.current_state == "CATEGORY_SELECTION"
        assert conv.whatsapp_state_json is None
    finally:
        db.close()


def test_session_expiry_mid_enquiry_discards_stale_answers():
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _open_travel_menu(db, phone)
        _say(db, phone, "1")
        _say(db, phone, "Qatar")
        db.refresh(conv)
        assert conv.whatsapp_state_json["travel_enquiry"]["answers"]

        conv.last_user_activity_at = datetime.now(timezone.utc) - timedelta(minutes=20)
        db.commit()

        res = _say(db, phone, "Khan")
        db.refresh(conv)
        assert res["status"] == "session_expired_awaiting_hi"
        assert conv.current_state == "START"
        assert conv.whatsapp_state_json is None
        assert conv.selected_category is None

        _say(db, phone, "Hi")
        db.refresh(conv)
        assert conv.current_state == "CATEGORY_SELECTION"
        assert conv.whatsapp_state_json is None
    finally:
        db.close()


def test_duplicate_confirm_creates_a_single_enquiry():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        email = _unique_email()
        _visa_to_review(db, phone, email=email)

        first = M._submit_travel_enquiry(db, conv)
        second = M._submit_travel_enquiry(db, conv)
        assert first["success"] is True
        assert second["status"] == "enquiry_already_submitted"
        db.refresh(conv)
        # Answers moved onto the Booking row; the session keeps only the reference.
        assert conv.whatsapp_state_json["travel_enquiry"]["answers"] == {}
        assert conv.whatsapp_state_json["travel_enquiry"]["submitted_ref"] == conv.booking_ref

        # A repeated Confirm from the customer (now in AWAITING_QUOTE) also must not
        # create a second row.
        _say(db, phone, "Confirm", input_id="btn_enquiry_confirm")
        db.refresh(conv)

        rows = db.query(Booking).filter_by(passenger_email=email).all()
        matching = [row for row in rows if row.service_type == "Visa Assistance"]
        assert len(matching) == 1
        assert matching[0].booking_ref == conv.booking_ref
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 9. Other menus stay untouched
# ---------------------------------------------------------------------------


def test_other_category_flows_are_unaffected():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _say(db, phone, "Hi")

        _say(db, phone, "1")                       # Airport Services
        db.refresh(conv)
        assert conv.current_state == "JOURNEY_TYPE_SELECTION"
        assert "journey type" in _last_buttons()["body_text"].lower()

        _say(db, phone, "0")                       # restart
        _say(db, phone, "4")                       # Hotel & Transportation
        db.refresh(conv)
        assert conv.current_state == "HOTEL_TRANSPORT_SUBMENU"
        assert [b["title"] for b in _last_buttons()["buttons"]] == ["Hotel Booking", "Transportation"]

        _say(db, phone, "0")
        _say(db, phone, "3")                       # Private Charter still uses the catalog menu
        db.refresh(conv)
        assert conv.current_state == "SERVICE_SELECTION"
        rows = WhatsAppClient.send_interactive_list.call_args.kwargs["sections"][0]["rows"]
        assert rows, "Private Charter should still list its catalog services"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Extra coverage: multi-applicant corporate visa, journey-type change, HELP
# ---------------------------------------------------------------------------


def test_corporate_visa_with_multiple_applicants_and_tentative_month():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _open_travel_menu(db, phone)
        _say(db, phone, "1")                    # Visa Assistance
        _say(db, phone, "United Kingdom")       # destination
        _say(db, phone, "India")                # passport country
        _say(db, phone, "United Kingdom")       # residence
        _say(db, phone, "3")                    # corporate delegation
        assert "company" in _last_text().lower()
        _say(db, phone, "Shafsky Pvt Ltd")
        _say(db, phone, "2")                    # business meeting
        _say(db, phone, "2")                    # tentative month
        _say(db, phone, "03/2027")
        _say(db, phone, "2 1 0")                # 2 adults + 1 child = 3 applicants

        for first, last, passport in (
            ("Aariz", "Khan", "A1234567"),
            ("Sara", "Khan", "B7654321"),
            ("Zoya", "Khan", "C1112223"),
        ):
            _say(db, phone, first)
            _say(db, phone, last)
            _say(db, phone, passport)
            _say(db, phone, "Skip")
            _say(db, phone, "Skip")

        _say(db, phone, "Aariz Khan")
        _say(db, phone, _unique_email())
        _say(db, phone, "Same")
        _say(db, phone, "Urgent - conference in March")

        review = _last_text()
        assert "March 2027" in review
        assert "Corporate delegation" in review
        assert "Shafsky Pvt Ltd" in review
        assert "3. Zoya Khan" in review

        _say(db, phone, "1", input_id="btn_enquiry_confirm")
        db.refresh(conv)
        booking = db.query(Booking).filter_by(booking_ref=conv.booking_ref).one()
        details = booking.metadata_json["details"]
        assert details["company"] == "Shafsky Pvt Ltd"
        assert details["behalf_of"] == "corporate"
        assert details["date_mode"] == "tentative"
        assert details["tentative_month"] == "2027-03"
        assert details["travel_date"] is None
        assert details["pax_adults"] == 3
        assert details["pax_children"] == 1
        assert len(details["applicants"]) == 3
        assert booking.service_options["service_date"] == "2027-03"
        assert "Behalf: CORPORATE" in booking.notes
        assert "Zoya Khan" in booking.notes
    finally:
        db.close()


def test_back_can_switch_ticketing_to_one_way_and_drops_return_step():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _open_travel_menu(db, phone)
        _say(db, phone, "2")                    # Flight Ticketing
        _say(db, phone, "2")                    # return journey
        _say(db, phone, "DEL")
        _say(db, phone, "BOM")
        _say(db, phone, FUTURE_DATE)
        assert "return date" in _last_text().lower()

        _say(db, phone, "BACK")                 # back to departure date
        _say(db, phone, "BACK")                 # back to arrival airport
        _say(db, phone, "BACK")                 # back to departure airport
        _say(db, phone, "BACK")                 # back to journey type
        assert "one-way or a return" in _last_text()

        _say(db, phone, "1")                    # now one-way
        _say(db, phone, "DEL")
        _say(db, phone, "BOM")
        _say(db, phone, FUTURE_DATE)
        # The return-date step no longer exists.
        assert "cabin class" in _last_text().lower()
        db.refresh(conv)
        answers = conv.whatsapp_state_json["travel_enquiry"]["answers"]
        assert answers["tkt_trip_type"] == "one_way"
    finally:
        db.close()


def test_help_inside_enquiry_keeps_the_current_step():
    db = SessionLocal()
    try:
        conv, phone = _new_conversation(db)
        _open_travel_menu(db, phone)
        _say(db, phone, "1")
        _say(db, phone, "Qatar")
        db.refresh(conv)
        before = conv.whatsapp_state_json["travel_enquiry"]["index"]

        res = _say(db, phone, "help")
        db.refresh(conv)
        assert res["status"] == "help_sent"
        assert conv.current_state == "TRAVEL_ENQUIRY"
        assert conv.whatsapp_state_json["travel_enquiry"]["index"] == before
    finally:
        db.close()
