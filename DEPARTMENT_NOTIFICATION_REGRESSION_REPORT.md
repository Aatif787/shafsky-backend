# Department WhatsApp Notification Regression — Investigation & Restoration

**Project:** Shafsky Aviation — FastAPI backend (`shafsky-backend-main`)
**Trigger:** after commit `c1a1161` (customer WhatsApp notification fix), the five operations numbers stopped receiving booking alerts.
**Date:** 2026-10-29
**Scope:** restore department notifications without reverting the customer improvements.

---

## 1. What the customer-notification commit actually touches

`git show c1a1161 --stat`:

```
app/services/booking_service_ext.py           |  67 ++-      (enquiry ack + admin confirm hook)
app/services/charter_service.py               |  19 +        (enquiry ack)
app/services/journey_engine_ext.py            |  13 +        (enquiry ack)
app/services/lounge_service.py                |   6 +        (enquiry ack)
app/services/notification_service_ext.py      | 598 +++--    (customer flow only)
app/services/ticketing_service.py             |  26 +        (enquiry ack)
app/services/whatsapp_department_service.py   |  64 ++-      (param flattening + phone normalisation)
tests/…, docs/…
```

The department pipeline is:

```
BookingService.create_booking() / create_service_enquiry()
  └─ NotificationService.notify_booking_created()          app/services/notification_service.py:926
       └─ WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification()
            ├─ claim_idempotency()            (template_type = WHATSAPP_OPS_NOTIFICATION only)
            ├─ get_recipient_phone_numbers()  (WHATSAPP_BOOKING_NOTIFY_PHONE_1..5)
            ├─ _resolve_template()            (WHATSAPP_DEPARTMENT_* only)
            └─ _send_to_recipient() × 5
```

**`notification_service.py` is not in the commit.** `notify_booking_created`, the dispatcher, the
recipient selection, the department routing table and the message formatter are byte-identical to the
previous release. `dispatch_booking_or_enquiry_notification` is still called from exactly one place
(`notification_service.py:926`).

The only department-facing lines the commit does change are:

1. `build_template_components()` now flattens the template parameter (no new-lines). Same length cap,
   same single parameter, same template name/language source.
2. `normalize_phone_number()` hardened. Re-verified against the five configured values (§2).

Neither removes a send.

## 2. Five recipients still resolve (verified, not assumed)

```
get_recipient_phone_numbers()
→ ['919599087957', '919599225778', '919217522660', '918864931247', '919599087959']
```

`9599087957 → 919599087957`, `9599225778 → 919599225778`, `9217522660 → 919217522660`,
`8864931247 → 918864931247`, `9599087959 → 919599087959`. Same order, same numbers, no duplicates.

## 3. Live-code reproduction of a department dispatch

Two runs with the WhatsApp client replaced by a recorder (no network, no DB):

| Configuration | Result |
| --- | --- |
| No department template (free-form, as in the repo `.env`) | **5 sends**, one per configured recipient, status `ACCEPTED` |
| Department template configured (`shafsky_ops_alert` / `en_US`, as the go-live checklist sets it) | **5 sends**, 1 body parameter each, 377 chars, 0 new-lines, status `ACCEPTED` |

Scripts: `scratch/check_department_dispatch.py`, `scratch/check_department_template_path.py`.

So the deployed code path itself still fans out to all five. That rules out triggers, routing, recipient
selection and message construction as the cause of a total silence.

## 4. What can silence all five — and what I found

`dispatch_booking_or_enquiry_notification` returns `BYPASSED` / `FAILED` (never raises) in these cases,
all of them configuration or Meta-side:

| Condition | Symptom in logs |
| --- | --- |
| `WHATSAPP_BOOKING_NOTIFY_PHONE_1..5` empty/cleared | `no recipient phones configured` (startup check also fails) |
| `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` missing | `credentials not configured` |
| `WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE` not approved on the WABA | per-recipient `code=132002/132001` |
| **`WHATSAPP_DEPARTMENT_TEMPLATE_LANG` not the registered language** | per-recipient `code=132001` |
| Template paused for quality | `code=132015/132000` |
| Free-form path and every number outside the 24-h window | `code=131047` |

**Defect found:** the department sender had no recovery for `132001`. One wrong character in
`WHATSAPP_DEPARTMENT_TEMPLATE_LANG` therefore fails **every** recipient, for **every** booking, with no
fallback — while the startup check still reports "ok", because it only verifies that the variables are
non-empty.

That is the highest-probability trigger here, and it sits next to the variables that were edited for the
customer fix: the customer template is registered as `en` while the operations template is `en_US`.
Setting the shared-looking language value to `en` makes `shafsky_ops_alert` unresolvable:

```
POST …/messages  template.name=shafsky_ops_alert  template.language=en
→ 404  132001  "Template name does not exist in the translation"   (no wamid)
```

I could not read the Render dashboard or its logs from here, so this is stated as the mechanism that fits
the evidence, not as a confirmed production fact. §7 lists the three values that settle it in one minute.

## 5. What was restored / hardened

1. **Language self-heal in the department sender** (`_send_to_recipient`): on `132001` it retries once
   with the sibling language code (`en` ⇄ `en_US`) and logs which variable to fix. `132001` is returned
   before a message is created, so the retry cannot duplicate a delivery. With this, a wrong language
   value no longer silences the five numbers.
2. **Startup diagnostics now show the resolved recipients and template language** (`validate_configuration`
   → `recipients_masked`, already logged by `main.py`), so a cleared recipient list is visible at boot.
3. **Per-recipient failure reporting unchanged but covered by tests**: every failed number keeps its
   Meta code, HTTP status and an actionable remedy in the log line and in `recipient_results`.
4. **Isolation guaranteed and tested**: the department dispatch runs first, in its own `try`, and no
   customer setting is read by the department modules (`grep WHATSAPP_CUSTOMER app/services/whatsapp_department*.py`
   → no matches). A customer failure cannot abort it.
5. **No duplicate sends**: idempotency claim unchanged and covered by a regression test.

Explicitly **not** done: no revert, no reset, no change to recipients, routing, booking persistence or
payment rules, and no change to the customer flow's behaviour.

## 6. Files changed in this pass

| File | Change |
| --- | --- |
| `app/services/whatsapp_department_service.py` | `alternate_template_language()` (en ⇄ en_US helper) |
| `app/services/whatsapp_department_ext.py` | `_send_to_recipient()` retries once on 132001 and logs the misconfiguration; `validate_configuration()` reports masked recipients |
| `tests/test_department_notification_regression.py` | new suite (7 tests) |

## 7. Test results (actual)

```
tests/test_department_notification_regression.py     7 passed
tests/test_whatsapp_department_notifications.py     18 passed   (full existing suite)
--------------------------------------------------------------
                                                    25 passed
```

New suite covers exactly the requested guarantees:

1. `test_booking_notifies_all_five_recipients` — one booking → 5 sends, to the five configured numbers, in order.
2. `test_department_dispatch_ignores_customer_template_settings` — customer template empty/invalid → still 5 sends.
3. `test_customer_notification_failure_does_not_block_department` — customer claim raising → department still 5 sends, and the customer call returns `FAILED` without raising.
4. `test_department_failure_is_reported_per_recipient` — Meta 131047 on every number → 5 entries with status `WINDOW_CLOSED` and a remedy each.
5. `test_recipients_are_the_five_configured_numbers` — routing unchanged.
6. `test_department_template_language_is_self_healed_on_132001` — `en` → retried as `en_US`, delivered.
7. `test_duplicate_event_is_not_sent_twice` — second dispatch of the same event is `SKIPPED`, no extra send.

## 8. What I need from the deployment to confirm the production cause

Render → `shafsky-backend` → Environment, read these five values:

| Variable | Expected |
| --- | --- |
| `WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE` | `shafsky_ops_alert` (or empty = free-form) |
| `WHATSAPP_DEPARTMENT_TEMPLATE_LANG` | `en_US` (the language `shafsky_ops_alert` is registered in) |
| `WHATSAPP_BOOKING_NOTIFY_PHONE_1..5` | the five numbers, unedited |

And one line from the Render logs after a test booking:

```
grep "\[WhatsApp Notify\]" 
```

* `code=132001` → the language value was changed; set it back to `en_US` (the new fallback already
  delivers in the meantime).
* `no recipient phones configured` → the five `WHATSAPP_BOOKING_NOTIFY_PHONE_*` values are gone.
* `credentials not configured` → token / phone-number-id.
* `code=131047` → free-form path without an approved department template.

## 9. Is a real delivery test still required?

**Yes.** Unit tests prove the code fans out to all five and heals a 132001, but they cannot prove
Meta accepted and delivered on the production WABA. After deploying this commit:

1. Create one booking on the website.
2. Check the Render log for `[WhatsApp Notify] … completed … 5/5 accepted by Meta`.
3. Confirm the alert actually appears on the five handsets.

No production setting was changed in this pass, and nothing was pushed.
