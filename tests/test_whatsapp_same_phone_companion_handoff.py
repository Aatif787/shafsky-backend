"""
Unit tests for the phone → companion-names handoff (no database required).

Locks the critical bug: a trailing comma after the companion prompt f-string
made `msg` a 1-tuple. Meta Graph API then received body as a JSON array and
rejected it, so after the customer typed "Same" with passenger_count > 1 the
chat went silent while state advanced to COMPANION_NAMES.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.integrations.whatsapp.handlers.details_flow import DetailsFlowMixin


class _Harness(DetailsFlowMixin):
    """Minimal stand-in so we can call mixin methods as classmethods."""

    @classmethod
    def _transition_state(cls, db, conv, new_state):
        conv.current_state = new_state


def test_same_with_two_passengers_sends_string_not_tuple():
    db = MagicMock()
    conv = SimpleNamespace(
        phone_number="919999999999",
        passenger_count=2,
        customer_phone=None,
        current_state="CUSTOMER_PHONE",
        flight_details_json=None,
    )
    sent = []

    with patch(
        "app.integrations.whatsapp.handlers.details_flow.whatsapp_client"
    ) as client:
        client.send_text_message.side_effect = lambda phone, body: sent.append(body) or {
            "success": True
        }
        result = _Harness._state_customer_phone(db, conv, "Same")

    assert result["status"] == "companion_names_prompt_sent"
    assert result["success"] is True
    assert conv.current_state == "COMPANION_NAMES"
    assert conv.customer_phone == "919999999999"
    assert len(sent) == 1
    assert isinstance(sent[0], str), f"Meta body must be str, got {type(sent[0]).__name__}"
    assert "Rahul Sharma" in sent[0]
    assert "1 more passenger" in sent[0]


def test_same_with_one_passenger_goes_straight_to_notes():
    db = MagicMock()
    conv = SimpleNamespace(
        phone_number="919888888888",
        passenger_count=1,
        customer_phone=None,
        current_state="CUSTOMER_PHONE",
    )
    sent = []

    with patch(
        "app.integrations.whatsapp.handlers.details_flow.whatsapp_client"
    ) as client:
        client.send_text_message.side_effect = lambda phone, body: sent.append(body) or {
            "success": True
        }
        result = _Harness._state_customer_phone(db, conv, "Same")

    assert result["status"] == "notes_prompt_sent"
    assert conv.current_state == "ADDITIONAL_REQUIREMENTS"
    assert isinstance(sent[0], str)
    assert "special requirements" in sent[0].lower()


def test_companion_back_returns_to_phone_and_prompts():
    db = MagicMock()
    conv = SimpleNamespace(
        phone_number="919777777777",
        passenger_count=2,
        current_state="COMPANION_NAMES",
        flight_details_json={"companion_names_partial": ["A"]},
    )
    sent = []

    with patch(
        "app.integrations.whatsapp.handlers.details_flow.whatsapp_client"
    ) as client:
        client.send_text_message.side_effect = lambda phone, body: sent.append(body) or {
            "success": True
        }
        result = _Harness._state_companion_names(db, conv, "back")

    assert result["status"] == "back_to_phone"
    assert conv.current_state == "CUSTOMER_PHONE"
    assert isinstance(sent[0], str)
    assert "Same" in sent[0]
