# WhatsApp Booking Notification Failure — Root Cause Analysis & Fix

**Scope:** `shafsky-backend-main` (FastAPI backend) — operations WhatsApp notifications for new bookings/enquiries.
**Date of investigation:** 2026-10-27
**Status:** Root cause identified. Code fixed and covered by tests. **Live delivery verified on the currently deployed revision.** The fix is **not deployed** (no authorization given).

> Secrets were never printed. Tokens appear only as length/prefix metadata; Meta errors are sanitized.

---

## 1. Root cause

### 1.1 What actually happens on a real booking

The production booking path is verified end to end:

```
POST /api/bookings                       (frontend: useAirportPayment.ts, AirportWorkflow.tsx)
  └─ BookingService.create_booking()     app/services/booking_service.py:172
       └─ NotificationService.notify_booking_created()   line 746
            └─ WhatsAppDepartmentService.dispatch_booking_or_enquiry_notification()
                 └─ WhatsAppClient.send_message() / send_text_message()
                      └─ POST https://graph.facebook.com/v21.0/{phone_number_id}/messages
```

Every other creation path also dispatches: `/api/bookings/enquiries`, `/api/airport/bookings`,
`/api/ticketing`, `/api/lounge`, `/api/charter`, `/api/journey` (service query) and the WhatsApp bot's
`review_payment_flow`.

### 1.2 Root cause A — the operations dispatch did not exist when the failed booking was created

`git log -S "dispatch_booking_or_enquiry_notification" app/services/notification_service.py` returns a
single commit:

| commit | date (IST) | change |
| --- | --- | --- |
| `13cef5b` | 2026-10-27 11:10 | first wiring of the 5-recipient operations dispatch into `notify_booking_created` |
| `bd3ae6d` | 2026-10-27 13:21 | idempotency/status hardening |
| `da86ba1` | 2026-10-27 14:09 | flight provider only (no WhatsApp change) |

Before `13cef5b`, `notify_booking_created()` **only suppressed the customer email** — it dispatched no
WhatsApp message at all.

Production database evidence (Neon, read-only queries):

* Bookings on record: `SHF-20261021-EE4C7F11`, `SHF-20261021-B92F8DDB`, `SHF-20261023-40112B1E`.
* `SELECT ... FROM notification_records WHERE template_type='WHATSAPP_OPS_NOTIFICATION'` returned **zero
  rows for any of those bookings** — the only rows before today were `SHF-TEST-CLASS` (a test).

So the booking that produced "none of the five received anything" was created under a revision that had no
operations dispatch to fail. Nothing was silently dropped — nothing was ever sent, and no unit test could
have caught it because the dispatch call itself did not exist.

**Confidence: high** for the database/git facts; the mapping to "the exact booking you created" rests on the
only real bookings in the production DB being from 2026-10-21/23.

### 1.3 Root cause B — no approved Meta template, so delivery is silently 24-hour-window dependent

This is the defect that will reproduce the same symptom again, and the one fixed in code.

* `WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE` is **empty** in the backend `.env`, and is **not declared** in
  `render.yaml`.
* The WhatsApp Business Account (`28382574078076685`) has **exactly one approved template**:
  `hello_world` / `en_US` / `UTILITY`. There is no operations-notification template.
* Consequently the dispatcher falls back to a **freeform text** message. Meta only delivers freeform text
  inside the recipient's **24-hour customer service window**. Outside it, Meta rejects every send with
  **error 131047** ("Re-engagement message").
* Before this fix the code logged that as a `WARNING`, recorded the recipient as a generic `FAILED`, and
  persisted no Meta error code — so the booking still succeeded and the failure was invisible in the UI,
  the admin notifications view, and the database.

Because the WABA has only `hello_world` approved, **any** non-empty value for
`WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE` in the Render dashboard would today fail with **132001**
("template name does not exist in the translation"). Either configuration is a defect: unset means
window-dependent delivery; set-but-unapproved means guaranteed failure for all five recipients.

### 1.4 Root cause C — the durable duplicate guard was dead code (real bug, proven)

`claim_idempotency()` looked up prior dispatches with:

```python
NotificationRecord.payload["idempotency_key"].astext == clean_key
```

`payload` is a **generic `JSON` column**, not `JSONB`, so this raises:

```
AttributeError: Neither 'BinaryExpression' object nor 'Comparator' object has an attribute 'astext'
```

The exception was swallowed with `logger.debug`, so the only cross-restart duplicate guard **never ran**.
Production `/ready` reports `redis: UNAVAILABLE`, and Redis is the other guard — leaving only per-process
memory with `WEB_CONCURRENCY=2`. Any restart or a second worker could therefore re-send notifications.

### 1.5 Contributing defects

| # | Defect | Effect |
| --- | --- | --- |
| D1 | Meta diagnostics (`status_code`, `error_code`, `error_subcode`, `error_user_msg`) discarded before logging/persisting | every non-timeout failure looked identical; window-closed vs. expired token vs. bad template indistinguishable |
| D2 | `render.yaml` never declared the 5 recipient numbers or the template/language | a blueprint-provisioned environment returns `BYPASSED/no_recipients_configured` (WARNING only) with no error surfaced |
| D3 | `_OfflineWhatsAppTransport` fabricates a Meta `200` + `wamid.sim_*` whenever `offline_third_party_calls()` is true (development **and** tests) | a local "it worked" proves nothing about live delivery |
| D4 | `BookingService.create_booking` and `AirportService.create_booking` never passed `service_category` | `multi_service` bookings were routed/labelled "Meet & Greet" regardless of the real category |
| D5 | Missing template logged at `WARNING` even in production | silent degradation in the exact environment that must not degrade silently |

---

## 2. Files changed

| File | Change |
| --- | --- |
| `app/services/whatsapp_department_service.py` | Durable-dedupe query fixed (no more `.astext`); added `META_FAILURE_REMEDIES` classification; new `_resolve_template`, `_send_to_recipient`, `_classify_meta_failure`, `_describe_send_result`, `validate_configuration`; both dispatchers now keep per-recipient Meta diagnostics and report `WINDOW_CLOSED`, `TEMPLATE_NOT_FOUND`, `AUTH_ERROR`, `RATE_LIMITED`, etc.; results now include `template_configured` and `status_counts`; template-less sends log `ERROR` in production |
| `app/integrations/whatsapp/client.py` | Returns `status_code` on success and `error_user_msg` on failure; logs an explicit `WARNING` when the offline/simulated transport is active |
| `app/services/booking_service.py` | Passes the resolved `service_category` into the notification context |
| `app/services/airport_service.py` | Same, for the Airport Meet & Greet engine |
| `app/main.py` | Startup diagnostic: logs `ERROR` (production) when recipients, credentials or template are missing |
| `render.yaml` | Declares `WHATSAPP_BOOKING_NOTIFY_PHONE_1..5`, `WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE`, `WHATSAPP_DEPARTMENT_TEMPLATE_LANG` |
| `.env.example` | Documents that an empty template means 24-hour-window-only delivery |
| `tests/test_whatsapp_department_notifications.py` | Autouse fixture now also purges persisted ops notifications (required because the durable guard actually works now) |
| `tests/test_whatsapp_notification_delivery_diagnostics.py` | **New** — 14 regression tests |

Not touched: frontend, WhatsApp bot handlers/flows, Razorpay, ICICI, booking prices, unrelated booking logic.

---

## 3. Configuration problems found

### 3.1 Verified from the credentials themselves (read-only Graph API)

| Check | Result |
| --- | --- |
| `WHATSAPP_PHONE_NUMBER_ID` → sender identity | `1198930063313277` → **+91 74283 00207**, "Shafsky Aviation Services", `platform_type: CLOUD_API`, `quality_rating: GREEN` — **correct sender** |
| `code_verification_status` | `EXPIRED` — display-name/registration verification lapsed; does not block sending but should be renewed in Meta Business Manager |
| `WHATSAPP_ACCESS_TOKEN` | `type: SYSTEM_USER`, `is_valid: true`, `expires_at: 0` (non-expiring), scopes `whatsapp_business_messaging`, `whatsapp_business_management`, `business_management` — **valid and correctly scoped** |
| Approved templates on WABA `28382574078076685` | **1** — `hello_world` / `en_US` / `UTILITY`. No operations template. |
| `WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE` | **unset** in `.env`; **absent** from `render.yaml` |
| `WHATSAPP_DEPARTMENT_TEMPLATE_LANG` | unset locally (code defaults to `en_US`); now declared in `render.yaml` |
| `WHATSAPP_BOOKING_NOTIFY_PHONE_1..5` | present locally; **absent** from `render.yaml` |
| Redis (production `/ready`) | `redis: UNAVAILABLE, configured: true` → in-memory idempotency only, per worker (`WEB_CONCURRENCY=2`) |

### 3.2 Must be checked in Render (values are not readable from the repo)

1. `WHATSAPP_BOOKING_NOTIFY_PHONE_1..5` — present, and holding **these five** numbers: `9599087957`,
   `9599225778`, `9217522660`, `8864931247`, `9599087959`. If any is missing, the dispatcher returns
   `BYPASSED/no_recipients_configured` and no message is attempted. (A successful send to all five was
   observed from this environment, but that only proves the values used here, not Render's.)
2. `WHATSAPP_ACCESS_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID` — compare against the values characterised in 3.1;
   a rotated/expired token is the classic "worked yesterday" failure.
3. `WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE` and `WHATSAPP_DEPARTMENT_TEMPLATE_LANG`.
4. Redis actually reachable (`REDIS_URL` from the `shafsky-redis` service) — otherwise duplicate suppression
   is per-process only.
5. That the running revision includes `13cef5b` or later (Deploy → commit hash).

---

## 4. Test results

### 4.1 Baseline (before any change)

Full suite on a freshly created isolated Postgres (`docker-compose.test.yml`, port 55432):

```
21 failed, 1059 passed, 5 warnings in 234.06s
```

All 21 failures were airport seed/production-configuration tests
(`test_ixr_*`, `test_blr_*`, `test_gox_*`, `test_del_*`, `test_bom_*`, `test_hyd_*`, `test_atq_*`).
They are **existing, unrelated** failures on a cold test database; they pass on a warm database. They were
not touched.

### 4.2 After the changes

```
1094 passed, 5 warnings in 198.76s        (full suite, run 1)
1094 passed, 5 warnings in 199.13s        (full suite, run 2, after the main.py tweak)
1093 passed, 1 failed, 5 warnings in 232.41s   (full suite, run 3)
```

Run 3 failed only `tests/test_workflow_admin.py::test_02_workflow_search`. That test **reproduces the same
failure with every file this change touches reverted** (`git checkout --`, verified), so it is a pre-existing
failure that depends on accumulated test-database rows pushing the searched instance out of the result page —
unrelated to WhatsApp and not caused by this change.

Targeted suites:

| Selection | Result |
| --- | --- |
| `test_whatsapp_notification_delivery_diagnostics.py` (new) | 14 passed |
| `test_whatsapp_department_notifications.py` | 18 passed |
| WhatsApp + notification + booking set (9 files) | 189 passed |
| notification + booking + integration + charter set (6 files) | 162 passed |
| WhatsApp + client + security regressions (5 files) | 165 passed |

Startup path verified separately in a real `ENVIRONMENT=production` boot (`RENDER=1`, template unset): the app
starts, `GET /ready` returns `200 READY`, and the log contains
`WhatsApp operations notifications are not fully configured: No approved template — set
WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE …` at `ERROR`, followed by a successful startup otherwise.

Static analysis on the two most-changed modules (`pylint --rcfile=.pylintrc`):
`9.79/10` for `whatsapp_department_service.py` (no new findings in the changed code; remaining items are
pre-existing local-import style used throughout the file) and no error-class findings in `client.py` beyond
pre-existing ones. `py_compile` clean on every changed module.

### 4.3 The new tests actually catch the old bugs

Running the new file against the pre-fix `whatsapp_department_service.py` (HEAD version, restored afterwards):

```
7 failed, 7 passed
```

The seven that fail are exactly the ones targeting the fixed behaviour: window-closed reporting, Meta
diagnostic preservation, durable dedupe across restart, production error logging, and
`validate_configuration`. This is the evidence that the tests are regression tests and not tautologies.

---

## 5. Actual Meta API responses observed

All values below are from real HTTP calls to `graph.facebook.com/v21.0`. Tokens are never echoed.

### 5.1 Read-only identity / token checks

| Call | HTTP | Sanitized result |
| --- | --- | --- |
| `GET /{phone_number_id}?fields=display_phone_number,verified_name,quality_rating,...` | **200** | `+91 74283 00207`, `Shafsky Aviation Services`, `GREEN`, `CLOUD_API`, `code_verification_status: EXPIRED` |
| `GET /debug_token` | **200** | `is_valid: true`, `expires_at: 0`, `type: SYSTEM_USER`, messaging + management scopes |
| `GET /{waba_id}/message_templates` | **200** | `total templates: 1` → `hello_world | en_US | APPROVED | UTILITY` |

### 5.2 Real sends (per-recipient, freeform text — the path the code actually used)

| Recipient | HTTP | `message_id` | Delivery webhook |
| --- | --- | --- | --- |
| `919****957` | 200 | `wamid.HBgMOTE5NTk5MDg3OTU3FQIAERgSM0Y2NTRCN0I1OTNEMTAyMzlGAA==` | delivered |
| `919****778` | 200 | `wamid.HBgMOTE5NTk5MjI1Nzc4FQIAERgSQjZEODREOEI5NTMwOUMxMTA2AA==` | delivered |
| `919****660` | 200 | `wamid.HBgMOTE5MjE3NTIyNjYwFQIAERgSMUIyNjk0NzgwNjk0MDlERDcyAA==` | delivered |
| `918****247` | 200 | `wamid.HBgMOTE4ODY0OTMxMjQ3FQIAERgSQUIwQjUxNjAzRDJCNTY5OTgwAA==` | delivered |
| `919****959` | 200 | `wamid.HBgMOTE5NTk5MDg3OTU5FQIAEg...RkQ2OTIxNjIxNjlGM0M1NDdCAA==` | delivered |

All five numbers are **currently inside their 24-hour window**, which is precisely why the template-less path
succeeds today and why a "test send" cannot prove the configuration is correct. No `131047` could be
produced without waiting for a window to close; the classification of `131047` is covered by a deterministic
test instead.

### 5.3 Real production bookings (live end-to-end)

| Booking | Request result | WhatsApp outcome |
| --- | --- | --- |
| `SHF-20261027-73036D8C` | HTTP **502** from Render's proxy (booking + notification still completed server-side) | 5/5 ACCEPTED → record later marked **DELIVERED**, `delivered_at` 08:40:20Z |
| `SHF-20261027-7C371712` | HTTP **201** in 6.3 s | 5/5 ACCEPTED → record later marked **DELIVERED**, `delivered_at` 08:40:56Z |

Both bookings produced a `notification_records` row with `template_type = WHATSAPP_OPS_NOTIFICATION`,
`recipient_phone = ALL_5_RECIPIENTS`, five real `wamid` values, and were then updated to `DELIVERED` by the
Meta status webhook — so the webhook path (`POST /api/whatsapp/webhook`) is working too
(`GET /api/whatsapp/webhook?hub.mode=subscribe` returns the challenge for the correct verify token and
**403** for a wrong one).

The `502` on the first attempt is a Render-proxy/cold-start artefact, not a notification failure; the retry
returned `201`. Worth a separate look because the frontend sees an error even though the booking exists.

---

## 6. Was live delivery verified?

**Yes — for the currently deployed revision (`da86ba1`).**

Verified: two real `POST /api/bookings` calls created real bookings; each produced five Meta-accepted
messages with real `wamid`s; the delivery-status webhook then marked both records `DELIVERED`. Sender
identity, token, and all five recipients are confirmed correct.

**Yes — for the fixed code:** verified that the fixed dispatcher still builds and sends the identical five
payloads (capturing transport, 5/5 ACCEPTED), and that the repaired durable guard now returns `DUPLICATE`
for an already-completed event against the production database, `ACQUIRED` for a new one and `IN_PROGRESS`
for an immediate repeat.

**No — the fix has not been deployed.** The running service still executes the old code, so the new
diagnostics, error classification and startup check are not live until a deploy is authorized.

**Automated tests vs. live delivery are kept separate throughout**: the 1094 passing tests use mocked or
offline transports and prove logic, not delivery. Only §5.2/§5.3 are live evidence.

---

## 7. What still has to happen (cannot be fixed in code alone)

1. **Create and approve one Meta template**, because Meta will not deliver business-initiated messages
   outside the 24-hour window without one. The code already sends exactly the right shape — a single `BODY`
   parameter — so the template must be:

   * name: e.g. `shafsky_ops_alert` (or any name you set in `WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE`)
   * language: `en_US` (must match `WHATSAPP_DEPARTMENT_TEMPLATE_LANG`)
   * category: `UTILITY`
   * body: one placeholder, e.g. `New Shafsky operations alert: {{1}}`

   Then set `WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE=shafsky_ops_alert` in Render. No further code change
   is required. (This can be created through the Meta API with the existing `whatsapp_business_management`
   scope, but that changes a live business asset, so authorization is needed first.)
2. **Apply `render.yaml`** (or add the same variables in the dashboard) so the five numbers and the template
   are declared rather than assumed.
3. **Deploy**, then confirm the startup log line no longer reports a missing template and place one test
   booking to see the template path (`template_configured: true`) instead of freeform. The startup check is
   wired into `app/main.py` and, when misconfigured, logs
   `WhatsApp operations notifications are not fully configured: <exact problem>` at `ERROR` in production.
4. **Renew** the phone number's `code_verification_status` (currently `EXPIRED`).
5. **Fix Redis reachability.** The repaired durable DB guard now covers restarts, so Redis is no longer
   load-bearing for that; it still matters for two workers racing on the *same* event at the same moment.

---

## 8. Residual risks (not changed, deliberately)

* `_dispatch_http` still retries `429` **and** `5xx` up to 3 times. A `5xx` may mean Meta already accepted
  the message, so a retry can duplicate an operations alert. This behaviour predates the change and was left
  alone to keep the diff reviewable; it is the remaining unsafe-retry path and should be narrowed to `429`.
* Idempotency keys fall back to a hash of email/phone/service/time when `booking_ref` is missing. Two
  identical anonymous enquiries within 24 hours would be treated as one event.
* The module is now 1282 lines (`pylint too-many-lines`); a split is advisable but out of scope.

---

## 9. Artefacts created in production during verification

Remove when convenient (they are diagnostic, not customer data):

* Bookings `SHF-20261027-73036D8C` and `SHF-20261027-7C371712` — status `PENDING`, passenger
  "Shafsky Ops Test", IXR/Silver, 2026-11-20 and 2026-11-21. Cancel/delete via the admin portal.
* `notification_records` rows with `template_type = WHATSAPP_OPS_NOTIFICATION` and idempotency keys
  `wa_booking_notify:SHF-HARNESS-0001`, `…:SHF-HARNESS-0002` (local harness runs) and the pre-existing
  `…:SHF-TEST-CLASS`.
