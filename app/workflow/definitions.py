"""
Configurable Workflow Definitions for Shafsky Aviation Service Domains.

Includes pre-built, configuration-driven state machines for:
1. Airport Meet & Assist (AIRPORT_MEET_AND_ASSIST)
2. Air Ticketing (AIR_TICKETING)
3. Hotel Booking (HOTEL_BOOKING)
4. Visa Assistance (VISA_ASSISTANCE)
5. Air Cargo (AIR_CARGO)
"""

import logging
from typing import Dict, Any
from sqlalchemy.orm import Session
from app.models.schema import WorkflowDefinition

logger = logging.getLogger("shafsky.workflow.definitions")

DEFAULT_WORKFLOW_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "AIRPORT_MEET_AND_ASSIST": {
        "name": "Airport Meet & Assist Workflow",
        "initial_state": "DRAFT",
        "states_config": {
            "DRAFT": {
                "allowed_actions": {
                    "CONFIRM": {"target": "BOOKED", "roles": ["CUSTOMER", "ADMIN", "CONCIERGE_TEAM"]}
                }
            },
            "BOOKED": {
                "allowed_actions": {
                    "ASSIGN_STAFF": {"target": "STAFF_ASSIGNED", "roles": ["ADMIN", "OPERATIONS_MANAGER", "DUTY_OFFICER"]},
                    "CANCEL": {"target": "CANCELLED", "roles": ["CUSTOMER", "ADMIN", "OPERATIONS_MANAGER"]}
                }
            },
            "STAFF_ASSIGNED": {
                "allowed_actions": {
                    "MEET_PASSENGER": {"target": "PASSENGER_MET", "roles": ["MEET_AND_ASSIST_STAFF", "OPERATIONS_MANAGER", "ADMIN"]},
                    "CANCEL": {"target": "CANCELLED", "roles": ["ADMIN", "OPERATIONS_MANAGER"]}
                }
            },
            "PASSENGER_MET": {
                "allowed_actions": {
                    "START_ASSISTANCE": {"target": "ASSISTANCE_IN_PROGRESS", "roles": ["MEET_AND_ASSIST_STAFF", "OPERATIONS_MANAGER", "ADMIN"]}
                }
            },
            "ASSISTANCE_IN_PROGRESS": {
                "allowed_actions": {
                    "COMPLETE": {"target": "COMPLETED", "roles": ["MEET_AND_ASSIST_STAFF", "OPERATIONS_MANAGER", "ADMIN"]}
                }
            },
            "COMPLETED": {"terminal": True},
            "CANCELLED": {"terminal": True},
            "REJECTED": {"terminal": True}
        }
    },
    "AIR_TICKETING": {
        "name": "Air Ticketing Workflow",
        "initial_state": "DRAFT",
        "states_config": {
            "DRAFT": {
                "allowed_actions": {
                    "CREATE_PNR": {"target": "PNR_CREATED", "roles": ["CUSTOMER", "ADMIN", "CONCIERGE_TEAM"]}
                }
            },
            "PNR_CREATED": {
                "allowed_actions": {
                    "VERIFY_PAYMENT": {"target": "PAYMENT_VERIFIED", "roles": ["SYSTEM", "FINANCE", "ADMIN"]},
                    "CANCEL": {"target": "CANCELLED", "roles": ["CUSTOMER", "ADMIN"]}
                }
            },
            "PAYMENT_VERIFIED": {
                "allowed_actions": {
                    "ISSUE_TICKET": {"target": "TICKET_ISSUED", "roles": ["CONCIERGE_TEAM", "ADMIN", "SYSTEM"]}
                }
            },
            "TICKET_ISSUED": {
                "allowed_actions": {
                    "COMPLETE": {"target": "COMPLETED", "roles": ["ADMIN", "SYSTEM"]},
                    "REFUND": {"target": "REFUNDED", "roles": ["FINANCE", "ADMIN"]}
                }
            },
            "COMPLETED": {"terminal": True},
            "CANCELLED": {"terminal": True},
            "REFUNDED": {"terminal": True}
        }
    },
    "HOTEL_BOOKING": {
        "name": "Hotel Booking Workflow",
        "initial_state": "DRAFT",
        "states_config": {
            "DRAFT": {
                "allowed_actions": {
                    "REQUEST_RESERVATION": {"target": "RESERVATION_REQUESTED", "roles": ["CUSTOMER", "ADMIN", "CONCIERGE_TEAM"]}
                }
            },
            "RESERVATION_REQUESTED": {
                "allowed_actions": {
                    "CONFIRM_HOTEL": {"target": "CONFIRMED_BY_HOTEL", "roles": ["CONCIERGE_TEAM", "ADMIN"]},
                    "REJECT": {"target": "REJECTED", "roles": ["CONCIERGE_TEAM", "ADMIN"]}
                }
            },
            "CONFIRMED_BY_HOTEL": {
                "allowed_actions": {
                    "CHECK_IN": {"target": "CHECKED_IN", "roles": ["CONCIERGE_TEAM", "ADMIN", "CUSTOMER"]},
                    "CANCEL": {"target": "CANCELLED", "roles": ["CUSTOMER", "ADMIN"]}
                }
            },
            "CHECKED_IN": {
                "allowed_actions": {
                    "CHECK_OUT": {"target": "CHECKED_OUT", "roles": ["CONCIERGE_TEAM", "ADMIN", "CUSTOMER"]}
                }
            },
            "CHECKED_OUT": {
                "allowed_actions": {
                    "COMPLETE": {"target": "COMPLETED", "roles": ["CONCIERGE_TEAM", "ADMIN"]}
                }
            },
            "COMPLETED": {"terminal": True},
            "CANCELLED": {"terminal": True},
            "REJECTED": {"terminal": True}
        }
    },
    "VISA_ASSISTANCE": {
        "name": "Visa Assistance Workflow",
        "initial_state": "DOCUMENT_COLLECTION",
        "states_config": {
            "DOCUMENT_COLLECTION": {
                "allowed_actions": {
                    "VERIFY_DOCUMENTS": {"target": "UNDER_VERIFICATION", "roles": ["CONCIERGE_TEAM", "ADMIN"]}
                }
            },
            "UNDER_VERIFICATION": {
                "allowed_actions": {
                    "SUBMIT_EMBASSY": {"target": "SUBMITTED_TO_EMBASSY", "roles": ["CONCIERGE_TEAM", "ADMIN"]},
                    "REJECT_DOCS": {"target": "DOCUMENT_COLLECTION", "roles": ["CONCIERGE_TEAM", "ADMIN"]}
                }
            },
            "SUBMITTED_TO_EMBASSY": {
                "allowed_actions": {
                    "APPROVE_VISA": {"target": "VISA_APPROVED", "roles": ["CONCIERGE_TEAM", "ADMIN"]},
                    "REJECT_VISA": {"target": "VISA_REJECTED", "roles": ["CONCIERGE_TEAM", "ADMIN"]}
                }
            },
            "VISA_APPROVED": {
                "allowed_actions": {
                    "COMPLETE": {"target": "COMPLETED", "roles": ["CONCIERGE_TEAM", "ADMIN"]}
                }
            },
            "VISA_REJECTED": {"terminal": True},
            "COMPLETED": {"terminal": True}
        }
    },
    "AIR_CARGO": {
        "name": "Air Cargo Workflow",
        "initial_state": "BOOKING_CREATED",
        "states_config": {
            "BOOKING_CREATED": {
                "allowed_actions": {
                    "RECEIVE_CARGO": {"target": "CARGO_RECEIVED", "roles": ["OPERATIONS_MANAGER", "DISPATCHER", "ADMIN"]}
                }
            },
            "CARGO_RECEIVED": {
                "allowed_actions": {
                    "CLEAR_CUSTOMS": {"target": "CUSTOMS_CLEARED", "roles": ["DISPATCHER", "ADMIN"]},
                    "REJECT": {"target": "REJECTED", "roles": ["DISPATCHER", "ADMIN"]}
                }
            },
            "CUSTOMS_CLEARED": {
                "allowed_actions": {
                    "DISPATCH_TRANSIT": {"target": "IN_TRANSIT", "roles": ["DISPATCHER", "DRIVER", "ADMIN"]}
                }
            },
            "IN_TRANSIT": {
                "allowed_actions": {
                    "DELIVER_CARGO": {"target": "DELIVERED", "roles": ["DRIVER", "DISPATCHER", "ADMIN"]}
                }
            },
            "DELIVERED": {
                "allowed_actions": {
                    "COMPLETE": {"target": "COMPLETED", "roles": ["DISPATCHER", "ADMIN"]}
                }
            },
            "COMPLETED": {"terminal": True},
            "REJECTED": {"terminal": True}
        }
    }
}


def seed_default_workflows(db: Session) -> Dict[str, str]:
    """
    Ensures that default workflow definitions for all 5 service domains exist in the database.
    """
    seeded = {}
    for service_type, config in DEFAULT_WORKFLOW_DEFINITIONS.items():
        existing = db.query(WorkflowDefinition).filter(
            WorkflowDefinition.service_type == service_type,
            WorkflowDefinition.is_active == True
        ).first()

        if not existing:
            wf_def = WorkflowDefinition(
                service_type=service_type,
                name=config["name"],
                version=1,
                initial_state=config["initial_state"],
                states_config=config["states_config"],
                is_active=True
            )
            db.add(wf_def)
            db.flush()
            seeded[service_type] = str(wf_def.id)
            logger.info(f"Seeded default workflow definition for '{service_type}' (ID {wf_def.id})")
        else:
            seeded[service_type] = str(existing.id)

    db.commit()
    return seeded
