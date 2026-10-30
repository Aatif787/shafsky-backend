# Customer WhatsApp Booking Notification — Why It Still Fails, and the Fix

**Project:** Shafsky Aviation — `shafsky-backend-main`
**Date:** 2026-10-30
**Symptom:** A customer books a service, enters their mobile number, and receives no WhatsApp message.
**Verdict:** The application code is now correct and *does* attempt the message. Meta accepts it (HTTP 200 + real `wamid`) and then refuses to deliver it with **error 131042 — no payment method on the WhatsApp Business Account**. No code change can bypass that gate; the fix is one billing action in Meta Business Manager.

---

## 1. Proof that the send path works

The last four customer notifications in the production database (`notification_records`):

| Booking | Template | Language | Param chars | HTTP | Meta `wamid` returned | Delivery result |
| --- | --- | --- | --- | --- | --- | --- |
| `SHF-20261029-7D58B18A` | `shafsky_booking_confirmation` | `en` | 218 | **200** | yes | **FAILED 131042** |
| `SHF-20261029-E0D3F8BD` | `shafsky_booking_confirmation` | `en` | 247 | **200** | yes | **FAILED 131042** |
| `SHF-20261029-6C549EE0` | `shafsky_booking_confirmation` | `en` | 255 | **200** | yes | **FAILED 131042** |
| `SHF-20261029-9F08C6E2` | `shafsky_booking_confirmation` | `en` | 219 | **200** | yes | **FAILED 131042** |

Recipient on all four: `917705881139` (masked), i.e. the number entered in the booking was normalized correctly and accepted by Meta.

The stored Meta error detail, verbatim:

```
code 131042 — Business eligibility payment issue
Message failed to send because no payment method is set up for your WhatsApp Business account.
Visit https://business.facebook.com/billing_hub/accounts/details/?business_id=1560549612203562
      &asset_id=28382574078076685&wizard_name=ADD_PM&account_type=whatsapp-business-account
```

That `asset_id` is this WABA (`28382574078076685`). The business id is `1560549612203562`.

## 2. Everything else was verified healthy (read-only)

| Check | Result |
| --- | --- |
| Template `shafsky_booking_confirmation` | **APPROVED**, language **`en`**, `UTILITY`, exactly one `{{1}}` |
| Sender `+91 74283 00207` | `CONNECTED`, `LIVE`, quality `GREEN`, `CLOUD_API` |
| WABA `28382574078076685` | `account_review_status: APPROVED`, currency `INR` |
| Access token | valid system-user token, non-expiring, correct scopes |
| `WHATSAPP_CUSTOMER_TEMPLATE_LANG` | `en` (the previous `en_US`/`132001` defect is gone) |
| `{{1}}` value | single line, no `\n`/`\t`, 218–255 chars (no `132000`/`132018`/`132005`) |
| Recipient | valid E.164 Indian mobile, accepted by Meta |
| Deployment timeline | commit `c1a1161` deployed 23:19 IST on 29 Oct; the first customer record appears 23:26 IST, seven minutes later |

So the pipeline is sound up to the last hop. The message is created, then Meta's billing gate blocks delivery. That is why the API returned success while the customer's phone stayed silent.

## 3. Why no earlier fix could work

The previous rounds fixed real defects — wrong template language (`en_US` → `132001`), new-lines inside `{{1}}`, missing customer path for enquiry-only services, phone normalization. Those were necessary, and the evidence above shows they now work. But they sit *before* Meta's billing check, so they could not make the message arrive while the WABA has no payment method.

A free-form message instead of a template would avoid billing, but Meta only accepts free-form inside the customer's 24-hour service window (`131047` otherwise). A customer filling a website form has not messaged the business, so that window is closed. There is no code path around this.

## 4. Code changes in this round (visibility only)

No send logic, template, recipient, timing or booking logic was changed.

| File | Change |
| --- | --- |
| `app/integrations/whatsapp/service.py` | Single-message customer records now store a readable failure (`Meta delivery failed (code 131042, Business eligibility payment issue): … no payment method …`) instead of a raw Python dict, persist `payload.delivery.error_code/error_detail/failed_at`, and log the actionable remedy. Operations records with a per-message map are untouched. |
| `app/services/whatsapp_department_service.py` | `131042` now has a precise remedy: add a card/prepaid balance in Billing Hub; if a card is already present, clear the "payment restricted" state. |
| `tests/test_whatsapp_notification_delivery_diagnostics.py` | Four regression tests: 131042 is persisted with its code and remedy, 131047 stays readable, delivery still marks `DELIVERED`, and the billing code has a documented remedy. |

Verified by direct execution of the webhook function (all assertions passed); the DB-backed pytest suite could not run here because local Postgres/Docker is not running. The change is confined to error formatting and logging.

## 5. The action that actually makes customers receive messages

In **Meta Business Manager**, for business `1560549612203562` / WABA `28382574078076685`:

1. Open the exact link from the error detail (the `ADD_PM` wizard):
   `https://business.facebook.com/billing_hub/accounts/details/?business_id=1560549612203562&asset_id=28382574078076685&wizard_name=ADD_PM&account_type=whatsapp-business-account`
2. Add a **credit card** or **prepaid balance** that can be charged in **INR**.
3. If Meta then reports *"payment restricted"* (same code 131042), complete business verification / clear the restriction in Business Settings → Business Info.
4. No redeploy is required — the app already sends the correct template.

Cost note: this template is category `UTILITY`. In India a utility template is a small per-message charge (roughly ₹0.10–0.20); free-form replies inside the 24-hour window remain free.

## 6. Post-fix verification (2 minutes)

1. Make one booking on the website with a number you control.
2. In `notification_records`, the row for `WHATSAPP_CUSTOMER_ENQUIRY_ACK` (enquiry) or `WHATSAPP_CUSTOMER_CONFIRMATION` (paid booking) should first be `COMPLETED`, then flip to `DELIVERED` when Meta's status webhook arrives.
3. If it stays `FAILED`, read `error_log` / `payload->delivery->error_code` — it now states the Meta code and the remedy instead of a raw dict.
4. The four historical `FAILED` rows are test bookings created before the billing fix; they are not retried automatically. A new booking is the correct test.

## 7. Deliberately unchanged

* No change to booking creation, pricing, payment, routing or department notifications.
* No change to the WhatsApp bot flows, menus or session handling.
* No new template, no new environment variable, no migration.
* No message was sent to a real customer during this investigation; all Meta calls were read-only except the historical sends already recorded by the application.
