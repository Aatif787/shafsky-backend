# Customer WhatsApp Booking Notification — Root Cause & Fix

**Project:** Shafsky Aviation — FastAPI backend (`shafsky-backend-main`)
**Subject:** Meta template `shafsky_booking_confirmation` is approved, yet customers never receive booking notifications on the number they enter in the website booking form.
**Date:** 2026-10-29
**Status:** Two independent blockers found and fixed in code; 29 regression tests green. One Render environment value must be corrected (§3).

---

## 1. What was verified against the live WhatsApp Business Account

Read-only Graph API calls using the deployment's own credentials. No token, secret or customer data is reproduced here.

| Fact | Value | How |
| --- | --- | --- |
| Template `shafsky_booking_confirmation` | id `1550520210442429`, **APPROVED**, category `UTILITY`, language **`en`**, `POSITIONAL`, quality `UNKNOWN` (the "Quality pending" badge) | `GET /v21.0/{WABA}/message_templates` and `GET /v21.0/1550520210442429` |
| All templates on the WABA | exactly 3, no pagination: `shafsky_booking_confirmation` (`en`), `shafsky_ops_alert` (`en_US`), `hello_world` (`en_US`) | same |
| Other language variants of that name | **none** — only the `en` variant exists | same |
| Template body | `Your booking with Shafsky Aviation has been received.\n\nBooking details:\n{{1}}\n\nOur team will assist you with your booking.` + footer | same |
| Template variables | **exactly one** positional `{{1}}` (example is a single-line summary) | same |
| Sender identity | `+91 74283 00207`, *Shafsky Aviation Services*, quality `GREEN`, `CLOUD_API` | `GET /v21.0/{PHONE_NUMBER_ID}` |
| Repository config | `WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE=shafsky_booking_confirmation`, `WHATSAPP_CUSTOMER_TEMPLATE_LANG=en` (`render.yaml` also sets `en`) | repo files |
| **Render dashboard (production)** | `WHATSAPP_CUSTOMER_TEMPLATE_LANG=**en_US**` | screenshot supplied by the owner |

---

## 2. Root cause A — production sends `en_US` for a template that only exists as `en`

The Render dashboard value overrides `render.yaml`, so every customer confirmation asked Meta for
`shafsky_booking_confirmation` in language `en_US`.

Meta stores this template as language **`en`** (the English base code). `en_US` is a *different*
translation of the same name, and it does not exist on this WABA — confirmed by listing every template
(3 total, no pagination) and by reading the template by id:

```json
{ "id": "1550520210442429", "name": "shafsky_booking_confirmation", "status": "APPROVED",
  "language": "en", "category": "UTILITY", "parameter_format": "POSITIONAL",
  "quality_score": { "score": "UNKNOWN" } }
```

In WhatsApp Manager the language column therefore shows the base English entry (`en`), not
"English (US)" — that is expected, not a missing template.

```
POST /v21.0/{phone_number_id}/messages
  template.name     = shafsky_booking_confirmation
  template.language = en_US
→ HTTP 404
  code      132001
  message   "(#132001) Template name does not exist in the translation"
  wamid     none
```

Measured on 2026-10-29 with the deployment's own credentials (`scratch/probe_template_language.py`).
Because no `wamid` is created, nothing is delivered — for **every** customer, every time. Department
notifications were unaffected because their template (`shafsky_ops_alert`) really is registered as
`en_US`.

This is the dominant cause of "template is Active, customers receive nothing".

**Fix**

* Operationally: set `WHATSAPP_CUSTOMER_TEMPLATE_LANG=en` in the Render dashboard (see §8). This is the
  authoritative change; the repository already carries the right value.
* In code: `_dispatch_customer_whatsapp()` now recognises `132001`, and when the configured language has a
  region (`en_US`) it retries **once** with the base language (`en`), logging an explicit error that names
  `WHATSAPP_CUSTOMER_TEMPLATE_LANG`. The retry cannot duplicate a delivery, because `132001` guarantees no
  message was created. If the base language also fails, the record keeps `FAILED` and its `error_log`
  now carries the language hint so the misconfiguration is visible in the admin queue.

---

## 3. Root cause B — the template variable contained new-lines

With the language corrected, the next failure was waiting: `send_customer_whatsapp_confirmation()` built a
formatted multi-line message with bullets and blank lines, then passed **that same string** into `{{1}}`:

```python
param_text = WhatsAppDepartmentService.sanitize_template_param(message_body)  # truncation only
components = [{"type": "body", "parameters": [{"type": "text", "text": param_text}]}]
```

`sanitize_template_param()` enforced only the 1024-character limit. Meta's other rules for template
parameters were not enforced: **no new-line characters, no tabs, no more than four consecutive spaces**.
Meta accepts the HTTP request but rejects the message (typically `132018` / `132000` / `132005`), so the
customer still receives nothing. Free-form department messages are allowed to contain new-lines, which is
why the department path looked healthy.

Measured on a representative booking (`scratch/simulate_customer_wa_payload.py`):

```
OLD {{1}}: 450 chars, 12 new-line characters, meta-legal: False
NEW {{1}}: 244 chars, 0 new-line characters, meta-legal: True
  'Booking reference: SHF-20261029-AB12CD34 | Guest: Rohan Sharma | Service: Arrival Meet & Greet |
   Airport: Indira Gandhi International Airport (DEL) | Date: 15 Nov 2026, 14:30 | Flight: 6E-2045 |
   Guests: 2 | Amount: INR 5,500 | Status: CONFIRMED'
```

**Fix**

* `build_template_parameter()` / `WhatsAppDepartmentService.to_template_parameter()` flatten every value
  rendered into `{{1}}`: CR/LF and tabs → one space, runs of spaces collapsed, hard cap 900 chars.
* The readable multi-line body is kept **only** for the free-form fallback (inside the 24-hour window).
* The `{{1}}` value is now a genuine single-line booking summary matching the example Meta approved.
* The same flattening is applied to the department template path, so an approved operations template
  cannot be rejected for the same reason. Free-form department sends are unchanged.

---

## 4. Root cause C — enquiry-only services never notified the customer at all

The website form has two persistence paths:

* `POST /api/bookings` — airport assistance with online payment; the customer confirmation is sent after
  payment verification (`dispatch_payment_fulfillment_side_effects` → `notify_booking_confirmed`).
* `POST /api/bookings/enquiries` — hotel, ground transport, lounge, ticketing, visa, medical, cargo;
  persisted as a **PENDING ₹0 quote-only booking**.

`notify_booking_created()` dispatched *only* the internal department WhatsApp alert. For enquiry-only
services there was **no customer channel at all**. Those customers never heard from the business.

**Fix** — `NotificationService.send_customer_whatsapp_enquiry_acknowledgement()`:

* reuses the same approved template (its body says the request *"has been received"*, accurate for an
  enquiry) — no new template, no Meta review needed;
* `{{1}}` becomes `Enquiry reference: … | Status: Enquiry received, quotation to follow (no payment taken)`;
* the word **CONFIRMED never appears** — an enquiry is not a booking;
* its own idempotency key (`WHATSAPP_CUSTOMER_ENQUIRY_ACK`), so it can neither suppress nor be suppressed
  by a later confirmation;
* skips bookings already `CONFIRMED`/`CANCELLED`/`REJECTED`/`COMPLETED`;
* refuses to send without a real ORM session, because an unpersisted claim could not prevent duplicates.

Triggered at every quote-only persistence point:

| File | Path it covers |
| --- | --- |
| `app/services/booking_service_ext.py` `create_service_enquiry()` | `/api/bookings/enquiries` (hotel, transport, medical, travel, cargo) |
| `app/services/journey_engine_ext.py` | service queries for unavailable airport services |
| `app/services/lounge_service.py` | lounge enquiry form |
| `app/services/charter_service.py` | private charter requests |
| `app/services/ticketing_service.py` | air-ticket requests (`NEW_BOOKING`, quoted by the desk) |

---

## 5. Root cause D — desk-confirmed bookings never notified the customer

`BookingService.admin_update_status()` moved a booking to `CONFIRMED` and returned. It sent **nothing**.
That is the only confirmation path for enquiries that are quoted and then confirmed by the desk (including
audited `force_confirm` for offline payment), so those customers were silent-billed.

**Fix** — after the status change is committed and refreshed, a transition `… → CONFIRMED` calls
`notify_booking_confirmed()` with the booking facts and an explicit `status: CONFIRMED`. The notification
layer is idempotent per booking, so a booking already confirmed by payment verification is skipped instead
of messaged twice. Failures are logged and never roll back the status change.

---

## 6. Root cause E — retry/dedupe and dishonest delivery status

1. `_acquire_notification_claim()` treated only `DELIVERED`/`BYPASSED` as terminal. A record in
   `COMPLETED` (Meta accepted, delivery unconfirmed) or `PENDING` (timeout, outcome unknown) fell through
   and **created a second record and sent again** — a duplicate message on every retry of the same event.
2. The customer method marked the record `DELIVERED` as soon as Meta returned HTTP 200 + `wamid`.
   Acceptance is not delivery.

**Fix**

* New `_customer_notification_already_handled()` checks the same notification identity
  (`template_type` + `booking_ref` + recipient) before claiming:
  * `DELIVERED` / `BYPASSED` / `COMPLETED` / `READ` / `OPENED` → never resend;
  * `PENDING` / `PROCESSING` → held; a timeout is never retried blindly (the Cloud API has no
    idempotency key);
  * `FAILED` → retry path preserved, bounded by `max_attempts`.
* Acceptance stores `COMPLETED`; only the existing status webhook (`apply_delivery_status`) may move the
  record to `DELIVERED`. A timeout stores `PENDING`.
* The record payload carries a `delivery` block: `template`, `template_language`, `parameter_chars`,
  `api_status`, `http_status`, `error_code`, `message_id`, `updated_at`.
* Logs include the masked recipient, template + language, HTTP status, Meta code/sub-code and the `wamid`.
  No token, secret or unmasked number is logged. The customer's full phone was removed from the stored
  payload (masked form only; the `recipient_phone` column remains the delivery address).

---

## 7. Phone normalisation

`WhatsAppDepartmentService.normalize_phone_number()` now handles every format the form and the bot can
produce, without duplicating the country code:

| Input | Output |
| --- | --- |
| `9599087957` | `919599087957` |
| `+91 95990-87957` | `919599087957` |
| `09599087957` | `919599087957` |
| `919599087957` | `919599087957` (unchanged) |
| `+91 098765 43210` | `919876543210` (trunk zero removed, 91 not duplicated) |
| `+1 415 555 2671` | `14155552671` |
| `+44 20 7946 0958` | `442079460958` |
| `0044 20 7946 0958` | `442079460958` |
| `123`, `0000000000`, 20 digits | `None` (rejected instead of sent to Meta) |

The frontend `PhoneInput` always submits `+<dial><national digits>`, so a non-Indian selection keeps its
own country code. Only a bare 10-digit number is treated as Indian, the form's documented default.

---

## 8. Deployment action required (blocking)

In the Render dashboard → `shafsky-backend` → Environment:

| Variable | Current | Required |
| --- | --- | --- |
| `WHATSAPP_CUSTOMER_TEMPLATE_LANG` | `en_US` | **`en`** |

Save and redeploy. This alone removes cause A. The code retry keeps customers receiving in the meantime,
but the configuration should still be corrected so the fallback is never exercised.

Also confirm `WHATSAPP_CUSTOMER_CONFIRMATION_TEMPLATE=shafsky_booking_confirmation` and
`WHATSAPP_PHONE_NUMBER_ID=1198930063313277` (both already correct).

---

## 9. Verification performed

* **Live template audit** — name, status, language `en`, category, single `{{1}}`, footer, no other
  language variant (§1).
* **Live language probe** — `en_US` returns HTTP 404 / `132001` with **no `wamid`** (§2). This is the
  decisive proof that production could never deliver, and that a language retry cannot duplicate a send.
* **Payload proof** — old vs new `{{1}}` for the same booking, with newline count and Meta legality
  (§3, `scratch/simulate_customer_wa_payload.py`).
* **Regression suite** — `tests/test_customer_whatsapp_delivery_fix.py` (new, database-free): **29 tests
  pass**, plus the 15 existing service-enquiry tests:
  * `{{1}}` has no `\n`, `\t` or double spaces, ≤1024 chars, exactly one parameter;
  * booking summary carries reference, guest, service, airport, date, flight, guests, amount, status;
  * free-form fallback keeps its readable multi-line layout;
  * `en_US` → single retry with `en` that succeeds; base-language `132001` → no retry, actionable
    `error_log`;
  * enquiry acknowledgement says "Enquiry", never "CONFIRMED";
  * accepted send stored as `COMPLETED` (not `DELIVERED`), `delivered_at` untouched;
  * duplicate events (`COMPLETED`, `PENDING`, `DELIVERED`) are not resent; `FAILED` stays retryable;
  * Indian, trunk-prefixed, US, UK, UAE and `00`-prefixed numbers normalise correctly; invalid rejected.
* **Type/syntax** — `python -m compileall app` clean; all edited modules import successfully.

Not performed (deliberately): no message was sent to any real customer number, and no production customer
phone was read. The only live POST used a dummy recipient and a template language that Meta rejects before
dispatch, so no message could reach anyone.

---

## 10. Post-deploy check (2 minutes)

1. Create one booking on the website with a phone you control.
2. In `notification_records`, expect a row with `template_type = WHATSAPP_CUSTOMER_CONFIRMATION` (paid
   booking) or `WHATSAPP_CUSTOMER_ENQUIRY_ACK` (enquiry), `status = COMPLETED`, and
   `payload->delivery->message_id` set.
3. The same row flips to `DELIVERED` when Meta's status webhook arrives (seconds to a minute), or to
   `FAILED` with the Meta code in `error_log`.
4. If `FAILED`, read `error_code` on the log line `[Customer WhatsApp] Send failed …`:
   * `132001` → template name/language mismatch — this is the Render variable from §8;
   * `132000` / `132018` / `132005` → parameter shape (should no longer occur);
   * `131047` → free-form fallback used without an approved template;
   * `131026` → recipient is not on WhatsApp / number mistyped;
   * `190` / `100` → token or Phone Number ID invalid.
5. Template quality shows `UNKNOWN` ("Quality pending") because it is new; it improves with delivered
   sends. No action needed, but monitor it in WhatsApp Manager.

---

## 11. Deliberately unchanged

* Meta template itself — verified correct; no new template created.
* Department WhatsApp notifications — same recipients, content and triggers; only the template-parameter
  formatting was made Meta-legal (free-form path untouched).
* WhatsApp webhook — no behavioural change; the existing `apply_delivery_status()` now receives truthful
  `COMPLETED` records.
* Payment/booking business rules — no confirmation is sent before the booking reaches `CONFIRMED`.

---

## 12. Files changed

| File | Change |
| --- | --- |
| `app/services/notification_service_ext.py` | Meta-legal template parameter + booking summary; 132001 language fallback; shared `_dispatch_customer_whatsapp()`; enquiry acknowledgement; true delivery status; duplicate guard; `en` language fallback |
| `app/services/whatsapp_department_service.py` | `to_template_parameter()`, flattened department template params, E.164 normalisation hardening |
| `app/services/booking_service_ext.py` | enquiry acknowledgement on `create_service_enquiry()`; customer confirmation on `CONFIRMED` transition |
| `app/services/journey_engine_ext.py` | enquiry acknowledgement for service queries |
| `app/services/lounge_service.py` | enquiry acknowledgement for lounge enquiries |
| `app/services/charter_service.py` | enquiry acknowledgement for charter requests |
| `app/services/ticketing_service.py` | enquiry acknowledgement for ticketing requests (`NEW_BOOKING`) |
| `tests/test_customer_whatsapp_delivery_fix.py` | new database-free regression suite (29 tests) |

No database migration is required. `NotificationStatus.COMPLETED/PENDING` already exist and are already
used by the operations dispatcher.
