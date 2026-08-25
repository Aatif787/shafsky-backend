"""Reliable WhatsApp outbound helpers: interactive send + guaranteed text fallback."""

import logging
from typing import Any, Dict, List, Optional

from app.integrations.whatsapp.copy import FOOTER

logger = logging.getLogger(__name__)


def _client(client=None):
    if client is not None:
        return client
    from app.integrations.whatsapp.client import whatsapp_client
    return whatsapp_client


def send_text(phone: str, message: str, client=None) -> Dict[str, Any]:
    wa = _client(client)
    try:
        res = wa.send_text_message(phone, message)
        if not res.get("success"):
            logger.warning("[WhatsApp Delivery] Text send failed: %s", res.get("status") or res.get("error"))
        return res
    except Exception as err:
        logger.error("[WhatsApp Delivery] Text send exception: %s", err)
        return {"success": False, "status": "failed", "error": str(err)}


def send_buttons(
    phone: str,
    body_text: str,
    buttons: List[Dict[str, str]],
    header_text: Optional[str] = None,
    fallback_text: Optional[str] = None,
    client=None,
) -> Dict[str, Any]:
    wa = _client(client)
    res = wa.send_interactive_buttons(
        to_phone=phone,
        body_text=body_text,
        buttons=buttons,
        header_text=header_text,
        footer_text=FOOTER,
    )
    if res.get("success"):
        return res

    lines = []
    for i, btn in enumerate(buttons, 1):
        lines.append(f"{i}. {btn.get('title', 'Option')}")
    text = fallback_text or (body_text + "\n\n" + "\n".join(lines) + "\n\nReply with the number of your choice.")
    send_text(phone, text, client=wa)
    return {**res, "success": True, "fallback_used": True}


def send_list(
    phone: str,
    body_text: str,
    button_title: str,
    sections: List[Dict[str, Any]],
    header_text: Optional[str] = None,
    fallback_text: Optional[str] = None,
    client=None,
) -> Dict[str, Any]:
    wa = _client(client)
    res = wa.send_interactive_list(
        to_phone=phone,
        body_text=body_text,
        button_title=button_title,
        sections=sections,
        header_text=header_text,
        footer_text=FOOTER,
    )
    if res.get("success"):
        return res

    text = fallback_text or body_text
    send_text(phone, text, client=wa)
    return {**res, "success": True, "fallback_used": True}
