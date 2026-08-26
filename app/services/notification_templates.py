from html import escape
from typing import Dict, Any


def _safe(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return escape(text) if text else default


class NotificationTemplateEngine:
    @classmethod
    def render_template(cls, template_type: str, data: Dict[str, Any]) -> Dict[str, str]:
        t_type = template_type.upper()
        
        name = _safe(data.get("passengerName") or data.get("passenger_name"), "Valued Guest")
        ref = _safe(data.get("bookingRef") or data.get("booking_ref"), "N/A")
        flight = _safe(data.get("flightNum") or data.get("flight_num"), "Flight")
        origin = _safe(data.get("originCode") or data.get("origin_code"), "Airport")
        dest = _safe(data.get("destCode") or data.get("dest_code"), "Destination")
        date_str = _safe(data.get("departureTime") or data.get("departure_time"), "Scheduled Time")
        
        raw_amt = data.get("totalAmount") if data.get("totalAmount") is not None else data.get("total_amount")
        try:
            amount_val = float(raw_amt) if raw_amt is not None else 0.0
            amount = f"{amount_val:,.2f}" if amount_val > 0 else "0.00"
        except (ValueError, TypeError):
            amount = _safe(raw_amt, "0.00")

        currency = _safe(data.get("currency"), "INR")
        passengers = _safe(data.get("passengerCount") or data.get("passenger_count") or data.get("passengers"), "")
        
        airport = _safe(data.get("airportCode") or data.get("airport_code") or origin)
        journey = _safe(data.get("journeyType") or data.get("journey_type") or data.get("serviceType") or data.get("service_type"))
        service = _safe(data.get("serviceName") or data.get("service_name") or data.get("package") or journey)
        phone = _safe(data.get("passengerPhone") or data.get("passenger_phone") or data.get("phone"))
        terminal = _safe(data.get("terminal"))
        status = _safe(data.get("status"), "PENDING")
        support = _safe(data.get("supportPhone"), "+91 9599087959")
        invoice_number = _safe(data.get("invoice_number") or data.get("invoiceNumber"))
        invoice_attached = bool(data.get("invoice_attached") or data.get("invoiceAttached"))
        raw_invoice_url = str(data.get("invoice_url") or data.get("invoiceUrl") or "").strip()
        invoice_url = raw_invoice_url if raw_invoice_url.startswith("https://") and not any(c in raw_invoice_url for c in '<>"\' \n\r\t') else ""

        if t_type == "BOOKING_CONFIRMATION":
            invoice_html = ""
            if invoice_attached:
                invoice_html = "<p>Your tax invoice is attached to this email.</p>"
                if invoice_number:
                    invoice_html = f"<p>Your tax invoice <strong>{invoice_number}</strong> is attached to this email.</p>"
            elif invoice_url:
                label = f"tax invoice {invoice_number}".strip() if invoice_number else "tax invoice"
                invoice_html = f'<p>Your tax invoice is available: <a href="{invoice_url}">Download {label}</a></p>'
            invoice_wa = f"\n\nYour tax invoice is available: {invoice_url}" if invoice_url else ""
            subject = f"Shafsky Aviation Services — Booking Confirmed ({ref})"
            html = f"""
            <h2>Shafsky Aviation Services VIP Services</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Your airport VIP service booking <strong>{ref}</strong> has been received and confirmed.</p>
            <ul>
                <li><strong>Airport:</strong> {airport}</li>
                <li><strong>Service type:</strong> {journey or service}</li>
                <li><strong>Package / service:</strong> {service}</li>
                <li><strong>Flight:</strong> {flight} ({origin} &rarr; {dest})</li>
                <li><strong>Date / time:</strong> {date_str}</li>
                {f"<li><strong>Passengers:</strong> {passengers}</li>" if passengers else ""}
                {f"<li><strong>Terminal:</strong> {terminal}</li>" if terminal else ""}
                <li><strong>Total Amount:</strong> {currency} {amount}</li>
                <li><strong>Status:</strong> {status}</li>
            </ul>
            {invoice_html}
            <p>Our 24/7 command desk: {support}</p>
            """
            whatsapp = f"✈️ *Shafsky Aviation Services VIP Desk*\n\nDear *{name}*,\n\nWe are delighted to confirm your VIP service booking.\n\n*Reference:* {ref}\n*Airport:* {airport}\n*Flight:* {flight}\n*Date:* {date_str}\n\nOur 24/7 command desk is at your disposal: {support}.{invoice_wa}"

        elif t_type == "BOOKING_RECEIVED":
            subject = f"Shafsky Aviation Services — Booking Received ({ref})"
            html = f"""
            <h2>Shafsky Aviation Services VIP Services</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>We have received your VIP service booking request <strong>{ref}</strong>.</p>
            <p>Your booking is currently pending payment confirmation. Please complete the payment to finalize your booking.</p>
            <ul>
                <li><strong>Airport:</strong> {airport}</li>
                <li><strong>Service type:</strong> {journey or service}</li>
                <li><strong>Package / service:</strong> {service}</li>
                <li><strong>Flight:</strong> {flight} ({origin} &rarr; {dest})</li>
                <li><strong>Date / time:</strong> {date_str}</li>
                {f"<li><strong>Passengers:</strong> {passengers}</li>" if passengers else ""}
                {f"<li><strong>Terminal:</strong> {terminal}</li>" if terminal else ""}
                <li><strong>Total Amount:</strong> {currency} {amount}</li>
                <li><strong>Status:</strong> PENDING PAYMENT</li>
            </ul>
            <p>If you have any questions, our 24/7 command desk is available: {support}</p>
            """
            whatsapp = f"✈️ *Shafsky Aviation Services VIP Desk*\n\nDear *{name}*,\n\nWe have received your VIP service booking request.\n\n*Reference:* {ref}\n*Airport:* {airport}\n*Flight:* {flight}\n*Date:* {date_str}\n\nYour booking is currently pending payment confirmation. Please complete the payment to finalize your booking.\n\nSupport: {support}."

        elif t_type == "ADMIN_NEW_BOOKING":
            subject = f"[Shafsky Ops] New booking {ref} — {airport}"
            html = f"""
            <h2>New booking received</h2>
            <ul>
                <li><strong>Reference:</strong> {ref}</li>
                <li><strong>Customer:</strong> {name}</li>
                <li><strong>Email:</strong> {data.get("passengerEmail") or data.get("passenger_email") or ""}</li>
                <li><strong>Phone:</strong> {phone}</li>
                <li><strong>Airport:</strong> {airport}</li>
                <li><strong>Journey:</strong> {journey}</li>
                <li><strong>Package / service:</strong> {service}</li>
                <li><strong>Flight:</strong> {flight} ({origin} &rarr; {dest})</li>
                <li><strong>Date / time:</strong> {date_str}</li>
                {f"<li><strong>Passengers:</strong> {passengers}</li>" if passengers else ""}
                {f"<li><strong>Terminal:</strong> {terminal}</li>" if terminal else ""}
                <li><strong>Total Amount:</strong> {currency} {amount}</li>
                <li><strong>Status:</strong> {status}</li>
            </ul>
            """
            whatsapp = f"🚨 *Operations Alert*\n\nNew booking received:\n*Ref:* {ref}\n*Guest:* {name}\n*Airport:* {airport}\n*Flight:* {flight}"

        elif t_type == "BOOKING_CANCELLED":
            reason = data.get("reason", "Cancelled upon request")
            subject = f"Shafsky Aviation Services - Booking Cancelled ({ref})"
            html = f"""
            <h2>Booking Cancelled</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Your booking <strong>{ref}</strong> for flight {flight} has been cancelled.</p>
            <p><strong>Reason:</strong> {reason}</p>
            """
            whatsapp = f"🛎️ *Shafsky Aviation Services Update*\n\nDear *{name}*,\n\nYour booking (*{ref}*) for flight {flight} has been cancelled.\n*Reason:* {reason}\n\nWe look forward to serving you in the future."

        elif t_type == "BOOKING_UPDATED":
            status = data.get("status", "UPDATED")
            subject = f"Shafsky Aviation Services - Booking Status Update ({ref})"
            html = f"""
            <h2>Booking Status Updated</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Your booking <strong>{ref}</strong> status is now: <strong>{status}</strong>.</p>
            """
            whatsapp = f"✨ *Shafsky Aviation Services Update*\n\nDear *{name}*,\n\nYour booking (*{ref}*) status has been updated to: *{status}*.\n\nPlease contact your concierge if you have any questions."

        elif t_type == "PAYMENT_SUCCESS":
            txn_id = data.get("transactionId", "TXN-OK")
            subject = f"Shafsky Aviation Services - Payment Receipt ({ref})"
            html = f"""
            <h2>Payment Successful</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Payment of <strong>{currency} {amount}</strong> received. Transaction ID: <code>{txn_id}</code>.</p>
            """
            whatsapp = f"💎 *Shafsky Aviation Services Billing*\n\nDear *{name}*,\n\nWe have successfully received your payment of *{currency} {amount}* for booking *{ref}*.\n\n*Transaction ID:* {txn_id}"

        elif t_type == "PAYMENT_FAILED":
            err = data.get("error", "Transaction declined")
            subject = f"Shafsky Aviation Services - Payment Failed ({ref})"
            html = f"""
            <h2>Payment Failed</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>We could not process payment for booking <strong>{ref}</strong>. Error: {err}</p>
            """
            whatsapp = f"⚠️ *Shafsky Aviation Services Billing*\n\nDear *{name}*,\n\nWe encountered an issue processing the payment for booking *{ref}*.\n*Error:* {err}\n\nPlease kindly retry your payment or contact support."

        elif t_type == "REMINDER":
            subject = f"Shafsky Aviation Services - Pre-Flight Service Reminder ({ref})"
            html = f"""
            <h2>Pre-Flight VIP Reminder</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>This is a reminder for your upcoming flight <strong>{flight}</strong> departing at {date_str}.</p>
            """
            whatsapp = f"⏰ *Shafsky Aviation Services Concierge*\n\nDear *{name}*,\n\nThis is a gentle reminder for your upcoming flight *{flight}* ({origin} -> {dest}) departing at *{date_str}*.\n\n*Reference:* {ref}\n\nOur team is preparing for your arrival."

        elif t_type == "FLIGHT_DELAY":
            new_time = data.get("newDepartureTime", "Updated Time")
            subject = f"Shafsky Aviation Services - Flight Schedule Alert ({flight})"
            html = f"""
            <h2>Flight Delay Alert</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Flight <strong>{flight}</strong> schedule has changed. New Estimated Departure: <strong>{new_time}</strong>.</p>
            """
            whatsapp = f"⚠️ *Shafsky Aviation Services Flight Alert*\n\nDear *{name}*,\n\nPlease be advised that flight *{flight}* has been delayed.\n*New Departure Time:* {new_time}\n\nOur concierge team will adjust your services accordingly."

        elif t_type == "FLIGHT_GATE_CHANGED":
            gate = data.get("gate", "TBA")
            terminal = data.get("terminal", "TBA")
            subject = f"Shafsky Aviation Services - Gate Update ({flight})"
            html = f"""
            <h2>Gate Change Alert</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Flight <strong>{flight}</strong> departure gate is now: <strong>Gate {gate} (Terminal {terminal})</strong>.</p>
            """
            whatsapp = f"🚪 *Shafsky Aviation Services Gate Update*\n\nDear *{name}*,\n\nThe departure gate for flight *{flight}* has been updated.\n*New Gate:* {gate} (Terminal {terminal})\n\nYour representative will guide you accordingly."

        elif t_type == "VIP_WELCOME":
            agent_name = data.get("agentName", "Airport Representative")
            agent_phone = data.get("agentPhone", "Duty Line")
            subject = f"Shafsky Aviation Services - Welcome to Airport ({origin})"
            html = f"""
            <h2>Welcome to {origin} Airport</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Your dedicated VIP Airport Representative <strong>{agent_name}</strong> is awaiting your arrival.</p>
            <p>Agent Contact: <strong>{agent_phone}</strong></p>
            """
            whatsapp = f"🌟 *Shafsky VIP Welcome*\n\nDear *{name}*,\n\nWelcome to *{origin}*!\n\nYour dedicated VIP Airport Representative, *{agent_name}*, is awaiting your arrival.\n*Agent Contact:* {agent_phone}"

        else:
            subject = f"Shafsky Aviation Services Notification ({ref})"
            html = f"<p>Dear <strong>{name}</strong>,</p><p>Notification regarding your booking <strong>{ref}</strong>.</p>"
            whatsapp = f"✨ *Shafsky Aviation Services Update*\n\nDear *{name}*,\n\nThere is an update regarding your booking *{ref}*.\n\nPlease contact our desk for details."

        return {
            "subject": subject,
            "html": html,
            "whatsapp_text": whatsapp
        }
