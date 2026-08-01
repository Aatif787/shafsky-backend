"""
Workflow Guard Condition Evaluator.

Evaluates declarative transition guards against request payloads:
- required_fields: Validates required keys exist and are non-empty.
- min_value: Validates numeric values meet minimum threshold rules.
- allowed_values: Validates string fields match permitted value sets.
- condition_rules: Evaluates key-value equality conditions.
"""

import logging
from typing import Dict, Any, Tuple, List

logger = logging.getLogger("shafsky.workflow.guards")


def evaluate_guards(guards_config: Dict[str, Any], payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Evaluates a dictionary of guard rules against an incoming transition payload.
    Returns Tuple[is_passed, List[error_messages]].
    """
    if not guards_config:
        return True, []

    errors: List[str] = []
    payload_dict = payload or {}

    # 1. Required Fields Guard
    required_fields = guards_config.get("required_fields", [])
    for field in required_fields:
        if field not in payload_dict or payload_dict[field] is None or payload_dict[field] == "":
            errors.append(f"Guard failed: Missing required payload field '{field}'.")

    # 2. Minimum Value Guard (e.g. {"total_amount": 100.0})
    min_value_rules = guards_config.get("min_value", {})
    for field, min_val in min_value_rules.items():
        if field in payload_dict and payload_dict[field] is not None:
            try:
                val = float(payload_dict[field])
                if val < float(min_val):
                    errors.append(f"Guard failed: Field '{field}' value {val} is below minimum allowed {min_val}.")
            except (ValueError, TypeError):
                errors.append(f"Guard failed: Field '{field}' must be a valid number.")

    # 3. Allowed Values Guard (e.g. {"currency": ["INR", "USD", "EUR"]})
    allowed_values_rules = guards_config.get("allowed_values", {})
    for field, permitted in allowed_values_rules.items():
        if field in payload_dict and payload_dict[field] is not None:
            val = str(payload_dict[field])
            if val not in permitted:
                errors.append(f"Guard failed: Field '{field}' value '{val}' is not in permitted set {permitted}.")

    # 4. Condition Rules Guard (e.g. {"payment_status": "COMPLETED"})
    condition_rules = guards_config.get("condition_rules", {})
    for field, expected_val in condition_rules.items():
        if field not in payload_dict or str(payload_dict[field]) != str(expected_val):
            actual_val = payload_dict.get(field, "None")
            errors.append(f"Guard failed: Condition rule for '{field}' expected '{expected_val}', got '{actual_val}'.")

    is_passed = len(errors) == 0
    if not is_passed:
        logger.warning(f"Workflow guard evaluation failed: {errors}")

    return is_passed, errors
