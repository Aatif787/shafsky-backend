"""
Enterprise AI Knowledge Base.
Provides structured company information, service policies, FAQs, supported airports, and escalation rules.
"""

from typing import Dict, Any, List

COMPANY_KNOWLEDGE: Dict[str, Any] = {
    "company": {
        "name": "Shafsky Aviation Services",
        "tagline": "World-Class Luxury Airport Concierge & Escort Services",
        "support_email": "concierge@shafsky.com",
        "hotline": "+91 1800-SHAFSKY"
    },
    "supported_airports": [
        {"iata": "BOM", "city": "Mumbai", "name": "Chhatrapati Shivaji Maharaj International Airport"},
        {"iata": "DEL", "city": "New Delhi", "name": "Indira Gandhi International Airport"},
        {"iata": "DXB", "city": "Dubai", "name": "Dubai International Airport"},
        {"iata": "LHR", "city": "London", "name": "Heathrow Airport"},
        {"iata": "JFK", "city": "New York", "name": "John F. Kennedy International Airport"},
        {"iata": "SIN", "city": "Singapore", "name": "Changi Airport"}
    ],
    "services": {
        "MEET_GREET": {
            "title": "Meet & Assist",
            "description": "Dedicated agent escorts guests through security, check-in, and baggage claim.",
            "sla_hours": 2
        },
        "FAST_TRACK": {
            "title": "Fast-Track Security & Immigration",
            "description": "Expedited priority lane access through immigration and security checkpoints.",
            "sla_hours": 1
        },
        "VIP_ASSIST": {
            "title": "VIP Tarmac & Lounge Transfer",
            "description": "Private limousine tarmac transfer to luxury airport lounges.",
            "sla_hours": 1
        },
        "LOUNGE": {
            "title": "Lounge Access",
            "description": "Premium airport lounge pass with gourmet dining and relaxation suites.",
            "sla_hours": 4
        }
    },
    "policies": {
        "cancellation": "Free cancellation up to 12 hours prior to scheduled flight departure. Cancellations within 12 hours incur a 20% service fee.",
        "amendment": "Flight time updates permitted free of charge up to 4 hours prior to departure.",
        "infants": "Children under 2 years old travel free of charge."
    },
    "faqs": [
        {
            "question": "Where does the concierge agent meet me?",
            "answer": "For arrivals, our agent meets you at the aerobridge exit with a name placard. For departures, at Curb-side Gate 1."
        },
        {
            "question": "Can I request buggy transport?",
            "answer": "Yes, electric buggy transit inside airport terminals can be added to any Meet & Assist package."
        }
    ],
    "escalation_rules": {
        "auto_handoff_triggers": ["complaint", "human", "vip", "operator", "urgent"],
        "max_failed_attempts": 3,
        "escalation_contact": "senior_ops_desk@shafsky.com"
    }
}


class AiKnowledgeService:
    """Provides structured enterprise knowledge queries."""

    @classmethod
    def get_knowledge_summary(cls) -> str:
        """Formats structured enterprise knowledge for LLM system prompt context."""
        airports_str = ", ".join([f"{a['iata']} ({a['city']})" for a in COMPANY_KNOWLEDGE["supported_airports"]])
        services_str = "; ".join([f"{code}: {info['title']}" for code, info in COMPANY_KNOWLEDGE["services"].items()])
        canc_policy = COMPANY_KNOWLEDGE["policies"]["cancellation"]

        return (
            f"COMPANY: {COMPANY_KNOWLEDGE['company']['name']} ({COMPANY_KNOWLEDGE['company']['tagline']}).\n"
            f"SUPPORTED AIRPORTS: {airports_str}.\n"
            f"SERVICES: {services_str}.\n"
            f"CANCELLATION POLICY: {canc_policy}.\n"
            f"CONTACT: {COMPANY_KNOWLEDGE['company']['hotline']} / {COMPANY_KNOWLEDGE['company']['support_email']}."
        )

    @classmethod
    def get_faqs(cls) -> List[Dict[str, str]]:
        return COMPANY_KNOWLEDGE["faqs"]
