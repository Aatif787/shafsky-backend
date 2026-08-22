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
        amount = _safe(data.get("totalAmount") or data.get("total_amount"), "0.00")
        currency = _safe(data.get("currency"), "INR")
        
        airport = _safe(data.get("airportCode") or data.get("airport_code") or origin)
        journey = _safe(data.get("journeyType") or data.get("journey_type") or data.get("serviceType") or data.get("service_type"))
        service = _safe(data.get("serviceName") or data.get("service_name") or data.get("package") or journey)
        phone = _safe(data.get("passengerPhone") or data.get("passenger_phone") or data.get("phone"))
        terminal = _safe(data.get("terminal"))
        status = _safe(data.get("status"), "PENDING")
        support = _safe(data.get("supportPhone"), "+91 9599087959")

        if t_type == "BOOKING_CONFIRMATION":
            subject = f"Shafsky Aviation — Booking Confirmed ({ref})"
            html = f"""
            <h2>Shafsky Aviation VIP Services</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Your airport VIP service booking <strong>{ref}</strong> has been received and confirmed.</p>
            <ul>
                <li><strong>Airport:</strong> {airport}</li>
                <li><strong>Service type:</strong> {journey or service}</li>
                <li><strong>Package / service:</strong> {service}</li>
                <li><strong>Flight:</strong> {flight} ({origin} &rarr; {dest})</li>
                <li><strong>Date / time:</strong> {date_str}</li>
                {f"<li><strong>Terminal:</strong> {terminal}</li>" if terminal else ""}
                <li><strong>Amount:</strong> {currency} {amount}</li>
                <li><strong>Status:</strong> {status}</li>
            </ul>
            <p>Our 24/7 command desk: {support}</p>
            """
            whatsapp = f"✈️ *Shafsky Aviation*: Booking confirmed. Ref *{ref}*. {airport} / {flight}. {date_str}."

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
                {f"<li><strong>Terminal:</strong> {terminal}</li>" if terminal else ""}
                <li><strong>Amount:</strong> {currency} {amount}</li>
                <li><strong>Status:</strong> {status}</li>
            </ul>
            """
            whatsapp = f"🚨 New booking *{ref}* — {name} — {airport} — {flight}"

        elif t_type == "BOOKING_CANCELLED":
            reason = data.get("reason", "Cancelled upon request")
            subject = f"Shafsky Aviation - Booking Cancelled ({ref})"
            html = f"""
            <h2>Booking Cancelled</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Your booking <strong>{ref}</strong> for flight {flight} has been cancelled.</p>
            <p><strong>Reason:</strong> {reason}</p>
            """
            whatsapp = f"⚠️ *Shafsky Aviation*: Booking *{ref}* for flight {flight} has been cancelled. Reason: {reason}."

        elif t_type == "BOOKING_UPDATED":
            status = data.get("status", "UPDATED")
            subject = f"Shafsky Aviation - Booking Status Update ({ref})"
            html = f"""
            <h2>Booking Status Updated</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Your booking <strong>{ref}</strong> status is now: <strong>{status}</strong>.</p>
            """
            whatsapp = f"ℹ️ *Shafsky Aviation*: Status update for booking *{ref}*: Current status is *{status}*."

        elif t_type == "PAYMENT_SUCCESS":
            txn_id = data.get("transactionId", "TXN-OK")
            subject = f"Shafsky Aviation - Payment Receipt ({ref})"
            html = f"""
            <h2>Payment Successful</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Payment of <strong>{currency} {amount}</strong> received. Transaction ID: <code>{txn_id}</code>.</p>
            """
            whatsapp = f"💳 *Shafsky Aviation*: Payment of {currency} {amount} received for booking *{ref}*. Transaction: {txn_id}."

        elif t_type == "PAYMENT_FAILED":
            err = data.get("error", "Transaction declined")
            subject = f"Shafsky Aviation - Payment Failed ({ref})"
            html = f"""
            <h2>Payment Failed</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>We could not process payment for booking <strong>{ref}</strong>. Error: {err}</p>
            """
            whatsapp = f"❌ *Shafsky Aviation*: Payment failed for booking *{ref}*. Error: {err}. Please retry your payment."

        elif t_type == "REMINDER":
            subject = f"Shafsky Aviation - Pre-Flight Service Reminder ({ref})"
            html = f"""
            <h2>Pre-Flight VIP Reminder</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>This is a reminder for your upcoming flight <strong>{flight}</strong> departing at {date_str}.</p>
            """
            whatsapp = f"⏰ *Shafsky Aviation*: Reminder for flight *{flight}* ({origin} -> {dest}) departing at {date_str}. Ref: *{ref}*."

        elif t_type == "FLIGHT_DELAY":
            new_time = data.get("newDepartureTime", "Updated Time")
            subject = f"Shafsky Aviation - Flight Schedule Alert ({flight})"
            html = f"""
            <h2>Flight Delay Alert</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Flight <strong>{flight}</strong> schedule has changed. New Estimated Departure: <strong>{new_time}</strong>.</p>
            """
            whatsapp = f"⚠️ *Shafsky Aviation*: Flight *{flight}* is delayed. New departure time: *{new_time}*."

        elif t_type == "FLIGHT_GATE_CHANGED":
            gate = data.get("gate", "TBA")
            terminal = data.get("terminal", "TBA")
            subject = f"Shafsky Aviation - Gate Update ({flight})"
            html = f"""
            <h2>Gate Change Alert</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Flight <strong>{flight}</strong> departure gate is now: <strong>Gate {gate} (Terminal {terminal})</strong>.</p>
            """
            whatsapp = f"🚪 *Shafsky Aviation*: Gate change for flight *{flight}*: Gate *{gate}*, Terminal *{terminal}*."

        elif t_type == "VIP_WELCOME":
            agent_name = data.get("agentName", "Airport Representative")
            agent_phone = data.get("agentPhone", "Duty Line")
            subject = f"Shafsky Aviation - Welcome to Airport ({origin})"
            html = f"""
            <h2>Welcome to {origin} Airport</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Your dedicated VIP Airport Representative <strong>{agent_name}</strong> is awaiting your arrival.</p>
            <p>Agent Contact: <strong>{agent_phone}</strong></p>
            """
            whatsapp = f"🌟 *Shafsky VIP*: Welcome to {origin}! Your airport representative *{agent_name}* is ready. Call: {agent_phone}."

        else:
            subject = f"Shafsky Aviation Notification ({ref})"
            html = f"<p>Dear <strong>{name}</strong>,</p><p>Notification regarding your booking <strong>{ref}</strong>.</p>"
            whatsapp = f"ℹ️ *Shafsky Aviation*: Update for booking *{ref}*."

        return {
            "subject": subject,
            "html": html,
            "whatsapp_text": whatsapp
        }
