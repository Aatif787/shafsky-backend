# WhatsApp Booking Notification Failure — Root Cause Analysis & Fix

**Scope:** `shafsky-backend-main` (FastAPI backend) — operations WhatsApp notifications for new bookings/enquiries.
**Date of investigation:** 2026-10-27
**Status:** Root cause proven per recipient. First batch of fixes is committed and pushed as `f271a3c`
(on `origin/main`, so Render has it). The per-message delivery tracking described here is **not yet committed
or deployed**.

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

### 1.3 Root cause B — no approved template, so Meta accepts and then fails delivery with 131047

**This is the defect that produces the reported symptom, and it is now proven, not inferred.**

The dispatcher sends a **freeform text** message because `WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE` is empty.
Meta's Cloud API accepts such a send — HTTP `200` plus a real `wamid` — and only decides delivery later, in the
status webhook. Outside the recipient's **24-hour customer service window** that verdict is:

```
code 131047  "Re-engagement message"
details: "Message failed to send because more than 24 hours have passed since the
          customer last replied to this number."
```

So acceptance proves nothing. Measured per recipient on 2026-10-27 (§5.2):

| Recipient | Kind | Last inbound to the business | Window | Meta verdict |
| --- | --- | --- | --- | --- |
| `8864931247` | personal | 2026-10-27 09:54 UTC (1,183 messages) | **open** | **DELIVERED** |
| `9599087957` | business | 2026-08-27 | closed | **FAILED 131047** |
| `9599225778` | business | 2026-08-25 | closed | **FAILED 131047** |
| `9217522660` | business | never | closed | **FAILED 131047** |
| `9599087959` | business | never | closed | **FAILED 131047** |

The correlation is exact: the only number that receives is the only number that chats with the bot, which is
also the only number whose window is open. Being a *WhatsApp Business* account is **not** the cause — see the
calibration in §5.4, where a genuinely API-registered number comes back as HTTP `400`, not `200`.

Consequences that made this invisible:

* the send path logged `accepted`/`ACCEPTED` and stored five `wamid`s, which looks healthy everywhere;
* nothing in the response, the logs, or the admin view said "131047";
* the record was then marked `DELIVERED` because **one** of the five messages reported delivered (§1.6).

Because the WABA has only `hello_world` approved, **any** non-empty value for
`WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE` would today fail with **132001** ("template name does not exist"),
and `hello_world` itself is unusable from a production number — Meta answers **131058**, "Hello World templates
can only be sent from the Public Test Numbers". A purpose-built template is therefore mandatory, not optional.

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

### 1.6 Root cause D — delivery status was stored per record, not per message

The status webhook handler wrote one flag on the record: any `delivered` status set
`NotificationRecord.status = DELIVERED`. With five messages per record, the single personal-number delivery
marked the whole notification delivered while four recipients had been rejected with 131047. That is why the
dashboard, and my own first read of it, showed a healthy delivery.

Fixed by keeping a per-message map (`payload.messages[]`, one entry per `wamid`) and deriving the record status
from every message: any failure makes the record `FAILED` with the Meta code preserved; `DELIVERED` requires
all five; otherwise the record stays `COMPLETED`.

---

## 2. Files changed

| File | Change |
| --- | --- |
| `app/services/whatsapp_department_service.py` | Durable-dedupe query fixed (no more `.astext`); `META_FAILURE_REMEDIES` classification; `_resolve_template`, `_send_to_recipient`, `_classify_meta_failure`, `_describe_send_result`, `validate_configuration`; per-recipient Meta diagnostics; `template_configured` and `status_counts`; **per-message map (`payload.messages[]`) written on every dispatch** so delivery can be attributed to a single recipient; template-less sends log `ERROR` in production |
| `app/integrations/whatsapp/service.py` | New `apply_delivery_status()`: records each webhook status against the exact `wamid` and derives the record status from **all** messages (any failure → `FAILED` with the Meta code; `DELIVERED` only when every message confirms); logs `ERROR` with remediation for 131047 |
| `app/integrations/whatsapp/client.py` | Returns `status_code` on success and `error_user_msg` on failure; warns when the simulated offline transport is active |
| `app/services/booking_service.py` | Passes the resolved `service_category` into the notification context |
| `app/services/airport_service.py` | Same, for the Airport Meet & Greet engine |
| `app/main.py` | Startup diagnostic: `ERROR` (production) naming the exact missing variable |
| `render.yaml` | Declares `WHATSAPP_BOOKING_NOTIFY_PHONE_1..5`, `WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE`, `WHATSAPP_DEPARTMENT_TEMPLATE_LANG` |
| `.env.example` | Documents that an empty template means 24-hour-window-only delivery |
| `tests/test_whatsapp_department_notifications.py` | Autouse fixture also purges persisted ops notifications (needed now that the durable guard actually works) |
| `tests/test_whatsapp_notification_delivery_diagnostics.py` | **New** — 17 regression tests, including three that pin per-message delivery attribution |

Not touched: frontend, WhatsApp bot conversation handlers/flows, Razorpay, ICICI review mode, booking prices,
unrelated booking logic.

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

Current run against `f271a3c` plus the per-message tracking:

```
1096 passed, 1 failed, 5 warnings in 195.92s
```

The single failure is the same pre-existing `test_02_workflow_admin.py::test_02_workflow_search`.

Targeted suites:

| Selection | Result |
| --- | --- |
| `test_whatsapp_notification_delivery_diagnostics.py` (17 tests) | 17 passed |
| `test_whatsapp_department_notifications.py` | 18 passed |
| diagnostics + department + integration | 52 passed |

Startup path verified separately in a real `ENVIRONMENT=production` boot (`RENDER=1`, template unset): the app
starts, `GET /ready` returns `200 READY`, and the log contains
`WhatsApp operations notifications are not fully configured: No approved template — set
WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE …` at `ERROR`, followed by a successful startup otherwise.

Static analysis on the two most-changed modules (`pylint --rcfile=.pylintrc`):
`9.79/10` for `whatsapp_department_service.py` (no new findings in the changed code; remaining items are
pre-existing local-import style used throughout the file) and no error-class findings in `client.py` beyond
pre-existing ones. `py_compile` clean on every changed module.

### 4.3 The new tests actually catch the old bugs

Two comparisons, both run and then restored:

* against the **original** pre-fix revision: `7 failed, 7 passed` — window-closed reporting, Meta diagnostic
  preservation, durable dedupe across restart, production error logging, and `validate_configuration` all fail;
* against **`f271a3c`** (the first batch, already deployed): `3 failed, 14 passed` — exactly the three new
  per-message delivery tests, because `apply_delivery_status` did not exist yet.

Each batch therefore fails on the code that preceded it, which is what makes these regression tests rather
than tautologies.

---

## 5. Actual Meta API responses observed

All values below are from real HTTP calls to `graph.facebook.com/v21.0`. No credential is echoed.

### 5.1 Read-only identity and capability checks

| Call | HTTP | Sanitized result |
| --- | --- | --- |
| `GET /{phone_number_id}` | **200** | `+91 74283 00207`, `Shafsky Aviation Services`, `GREEN`, `CLOUD_API`, `CONNECTED`, `code_verification_status: EXPIRED` |
| `GET /debug_token` | **200** | `is_valid` true, `expires_at` 0, type `SYSTEM_USER`, scopes include messaging and management |
| `GET /{waba_id}/message_templates` | **200** | one template only — `hello_world` / `en_US` / `UTILITY` |
| `GET /{waba_id}/phone_numbers` | **200** | one number, the sender `+91 74283 00207`; none of the five recipients is registered as an API sender on this WABA |
| `GET /{business_id}/owned_whatsapp_business_accounts` | **200** | two WABAs; the second holds only a `+1 555` test number |
| `GET /{waba_id}/conversation_analytics` | **200** | empty dataset — Meta exposes no per-message delivery status over the API |
| `GET /{message_id}` | **400** | `GraphMethodException`: a message id is not readable, so status exists only in webhooks |

### 5.2 Per-recipient send and delivery (controlled experiment, 2026-10-27)

One labelled freeform message per number, with its `wamid` recorded so Meta's status webhook could be attributed
to the exact recipient.

| # | Recipient | Attempted | Meta accepted | Delivered | Evidence |
| --- | --- | --- | --- | --- | --- |
| 1 | `9599087957` | yes, own request | `200` + `wamid.HBgMOTE5NTk5MDg3OTU3FQ…` | **no** | 131047 "more than 24 hours have passed since the customer last replied" |
| 2 | `9599225778` | yes, own request | `200` + `wamid.HBgMOTE5NTk5MjI1Nzc4…` | **no** | 131047 |
| 3 | `9217522660` | yes, own request | `200` + `wamid.HBgMOTE5MjE3NTIyNjYw…` | **no** | 131047 |
| 4 | `8864931247` | yes, own request | `200` + `wamid.HBgMOTE4ODY0OTMxMjQ3…` | **yes** | webhook `delivered`, `delivered_at` set |
| 5 | `9599087959` | yes, own request | `200` + `wamid.HBgMOTE5NTk5MDg3OTU5…` | **no** | 131047 |

So the answer to "rejected, accepted-but-not-delivered, or never attempted" is: **all five attempted as five
independent requests, all five accepted by Meta, four never delivered** — rejected at delivery because the
24-hour window is closed.

### 5.3 Real production bookings

Both bookings created during the earlier investigation produced five accepted messages; the record was then
marked `DELIVERED` on the strength of the single personal-number delivery (§1.6). With per-message tracking in
place, the same run would now read `FAILED … (codes: [131047]) … 4/5`.

| Booking | Request result | Notification record |
| --- | --- | --- |
| `SHF-20261027-73036D8C` | HTTP **502** from Render's proxy (booking still committed) | 5 accepted; recorded `DELIVERED` — actually 1 delivered, 4 × 131047 |
| `SHF-20261027-7C371712` | HTTP **201** in 6.3 s | 5 accepted; recorded `DELIVERED` — actually 1 delivered, 4 × 131047 |

### 5.4 Calibration — what a genuinely ineligible recipient looks like

Two control sends prove the four business numbers are **not** the problem:

| Control | HTTP | Response |
| --- | --- | --- |
| Send to our own API sender number `7428300207` (registered on the Cloud API, cannot receive) | **400** | `(#100) Invalid parameter` |
| Send `hello_world` from the production number | **400** | `(#131058) Hello World templates can only be sent from the Public Test Numbers` |

A real Business *Platform* recipient fails synchronously with `400`. The four recipients returned `200` with a
`wamid`, so they are ordinary, eligible WhatsApp users — including WhatsApp Business app accounts. The failure
is the window, not the account type.

## 6. Was live delivery verified?

**Yes, and it is verified to be broken for four of the five recipients.**

* All five numbers: request attempted independently, `200` accepted with a `wamid` (§5.2).
* `8864931247`: delivered, confirmed by the status webhook. Preserved by the fix.
* The other four: not delivered, Meta code **131047**, confirmed by the status webhook.
* Production bookings reproduce it: five accepted, one delivered.

What is **not** yet true is delivery to the four business numbers. No code change can fix that on its own: Meta
will not deliver a business-initiated freeform message to a recipient who has not written in 24 hours. The fix
is an approved template (§7 step 1), after which the same dispatcher sends window-independently.

**Automated tests versus live evidence:** the 1,094-test suite uses mocked transports and proves logic only;
every claim about delivery above comes from real Meta responses or the real status webhook.

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
* Six more rows from the per-recipient delivery experiment (§5.2) — idempotency keys prefixed
  `wa_diagnostic_probe:` and `payload.probe = true`. The cleanup SQL in the go-live checklist removes them
  together with the harness rows.
* Probe messages were delivered to the five numbers themselves (labelled "automated infrastructure test").
