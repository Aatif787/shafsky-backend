from typing import Dict, Any

class NotificationTemplateEngine:
    @classmethod
    def render_template(cls, template_type: str, data: Dict[str, Any]) -> Dict[str, str]:
        t_type = template_type.upper()
        
        name = data.get("passengerName", data.get("passenger_name", "Valued Guest"))
        ref = data.get("bookingRef", data.get("booking_ref", "N/A"))
        flight = data.get("flightNum", data.get("flight_num", "Flight"))
        origin = data.get("originCode", data.get("origin_code", "Airport"))
        dest = data.get("destCode", data.get("dest_code", "Destination"))
        date_str = data.get("departureTime", data.get("departure_time", "Scheduled Time"))
        amount = data.get("totalAmount", data.get("total_amount", "0.00"))
        currency = data.get("currency", "INR")
        
        if t_type == "BOOKING_CONFIRMATION":
            subject = f"Shafsky Aviation Concierge - Booking Confirmed ({ref})"
            html = f"""
            <h2>Shafsky Aviation VIP Concierge</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Your airport concierge booking <strong>{ref}</strong> has been successfully confirmed!</p>
            <ul>
                <li><strong>Flight:</strong> {flight} ({origin} &rarr; {dest})</li>
                <li><strong>Departure Time:</strong> {date_str}</li>
                <li><strong>Amount Paid:</strong> {currency} {amount}</li>
            </ul>
            <p>Our dedicated VIP ground team will meet you at the airport.</p>
            """
            whatsapp = f"✈️ *Shafsky Aviation*: Booking Confirmed! Ref: *{ref}*. Flight: {flight} ({origin} -> {dest}). Departure: {date_str}. Our VIP concierge team is ready to assist you!"

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
            subject = f"Shafsky Aviation - Pre-Flight Concierge Reminder ({ref})"
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
            agent_name = data.get("agentName", "Concierge Agent")
            agent_phone = data.get("agentPhone", "Duty Line")
            subject = f"Shafsky Aviation - Welcome to Airport ({origin})"
            html = f"""
            <h2>Welcome to {origin} Airport</h2>
            <p>Dear <strong>{name}</strong>,</p>
            <p>Your dedicated VIP Concierge Agent <strong>{agent_name}</strong> is awaiting your arrival.</p>
            <p>Agent Contact: <strong>{agent_phone}</strong></p>
            """
            whatsapp = f"🌟 *Shafsky VIP*: Welcome to {origin}! Your concierge agent *{agent_name}* is ready. Call: {agent_phone}."

        else:
            subject = f"Shafsky Aviation Notification ({ref})"
            html = f"<p>Dear <strong>{name}</strong>,</p><p>Notification regarding your booking <strong>{ref}</strong>.</p>"
            whatsapp = f"ℹ️ *Shafsky Aviation*: Update for booking *{ref}*."

        return {
            "subject": subject,
            "html": html,
            "whatsapp_text": whatsapp
        }
