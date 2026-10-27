# WhatsApp Operations Notifications — Go-Live Checklist

Ordered, copy-pasteable runbook for releasing the fix. Every step has a pass/fail check.
All values below are read from the environment; no credentials are written in this document.

**Current state of the branch:** code complete, `1094 passed` on the full suite, **not deployed**.
The only things standing between the branch and reliable delivery are step 1 and step 3.

---

## 0. Pre-flight (2 minutes, read-only)

| # | Check | Expected |
| --- | --- | --- |
| 0.1 | `GET https://shafsky-backend-1.onrender.com/ready` | `{"ready":true}`; note whether `redis.status` is `UNAVAILABLE` |
| 0.2 | Sender identity via `GET /v21.0/{WHATSAPP_PHONE_NUMBER_ID}?fields=display_phone_number,verified_name,quality_rating` | `+91 74283 00207`, `Shafsky Aviation Services`, `GREEN` |
| 0.3 | Validity and scopes via the Graph `debug_token` endpoint | `is_valid` true, `expires_at` 0, messaging scope present |
| 0.4 | Approved templates via `GET /v21.0/{WABA_ID}/message_templates` | today only `hello_world` — hence step 1 |

---

## 1. Create and approve the operations template (blocking)

Business-initiated WhatsApp messages outside the recipient's 24-hour window are impossible without an
approved template. The dispatcher already sends exactly one `BODY` parameter, so the template must have
**exactly one** variable.

```bash
curl -X POST "https://graph.facebook.com/v21.0/$WHATSAPP_BUSINESS_ACCOUNT_ID/message_templates" \
  --oauth2-bearer "$WHATSAPP_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "name": "shafsky_ops_alert",
        "language": "en_US",
        "category": "UTILITY",
        "components": [
          {
            "type": "BODY",
            "text": "New Shafsky operations alert: {{1}}",
            "example": {
              "body_text": [["SHF-20261027-7C371712 | Meet & Greet | DEL | 2 pax"]]
            }
          }
        ]
      }'
```

**Check:** repeat 0.4 and confirm `shafsky_ops_alert | en_US | APPROVED`.
`PENDING` means Meta is still reviewing — wait, then re-run the check; the template must be `APPROVED`
before step 3. If you prefer a different name, use it consistently in step 2 and in the API call.

> Why `UTILITY` and one parameter: the alert is a transactional operational notice, and
> `build_template_components()` always emits a single body parameter carrying the full formatted alert.
> A template with 0 or 2+ variables fails with **132000**.

---

## 2. Set the Render environment variables

Dashboard → `shafsky-backend` → Environment. `render.yaml` now declares all of these, so a Blueprint sync
prompts for them; dashboard values win either way.

| Name | Value |
| --- | --- |
| `WHATSAPP_BOOKING_NOTIFY_PHONE_1` | `9599087957` |
| `WHATSAPP_BOOKING_NOTIFY_PHONE_2` | `9599225778` |
| `WHATSAPP_BOOKING_NOTIFY_PHONE_3` | `9217522660` |
| `WHATSAPP_BOOKING_NOTIFY_PHONE_4` | `8864931247` |
| `WHATSAPP_BOOKING_NOTIFY_PHONE_5` | `9599087959` |
| `WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE` | `shafsky_ops_alert` |
| `WHATSAPP_DEPARTMENT_TEMPLATE_LANG` | `en_US` |
| `WHATSAPP_ACCESS_TOKEN` | unchanged — confirm it matches 0.3 |
| `WHATSAPP_PHONE_NUMBER_ID` | `1198930063313277` (resolves to +91 74283 00207) |

Ten-digit numbers are normalised to `91XXXXXXXXXX` automatically; `+91` and spaces are also accepted.

**Check:** after the deploy in step 3, the startup log must **not** contain
`WhatsApp operations notifications are not fully configured`.

---

## 3. Deploy

```bash
git add -A
git commit -m "fix(whatsapp): reliable ops notifications (template path, Meta diagnostics, durable dedupe)"
git push origin main          # Render autoDeployTrigger: commit
```

**Check:** Render → Logs, first 30 seconds after boot:

* **PASS** — no `WhatsApp operations notifications are not fully configured` line.
* **FAIL** — the line is present and now names the exact problem, e.g.
  `… No approved template — set WHATSAPP_DEPARTMENT_NOTIFICATION_TEMPLATE …` → fix step 1 or 2, redeploy.

---

## 4. Verify with one real booking

1. Create one booking through the site (or `POST /api/bookings`).
2. Read the persisted dispatch record (read-only):

```sql
SELECT created_at, status, message_id,
       payload->>'outcome'         AS outcome,
       payload->'status_counts'    AS counts,
       payload->'recipient_results' AS results
FROM notification_records
WHERE template_type = 'WHATSAPP_OPS_NOTIFICATION'
ORDER BY created_at DESC
LIMIT 1;
```

**PASS criteria**

| Signal | Meaning |
| --- | --- |
| `outcome` is `ACCEPTED` | every configured recipient was accepted by Meta |
| `counts` is `{"ACCEPTED": 5}` | all five accepted, none failed |
| five `wamid.…` values in `message_id` | Meta issued real message IDs |
| the row becomes `DELIVERED` within about a minute | the status webhook confirms handset delivery |
| all five numbers physically received the alert | the only check that proves delivery |

> `outcome` and `status_counts` are persisted by this fix. On the currently deployed revision they are
> absent, so an empty column here means you are still running the old code.

**FAIL triage** — the record now carries the exact Meta code for every recipient:

| Per-recipient status | Meta code | Fix |
| --- | --- | --- |
| `WINDOW_CLOSED` | 131047 | template not set or not approved → steps 1–2 |
| `TEMPLATE_NOT_FOUND` | 132001 | name or language does not match the approved template |
| `TEMPLATE_PARAM_MISMATCH` | 132000 / 132005 | template body must have exactly one `{{1}}` |
| `AUTH_ERROR` | 190 | credential rotated or expired → issue a new system-user token |
| `RATE_LIMITED` | 130429 / 131048 | burst volume; back off, not a configuration bug |
| `UNDELIVERABLE` / `INVALID_RECIPIENT` | 131026 / 131030 | number not on WhatsApp or not in the allowed list |

---

## 5. Rollback

Code-only, no migration: `git revert <commit>` and push. The `render.yaml` additions are new names only, so
reverting restores the previous behaviour (freeform sends, warning-level logging). Nothing in this change
alters the database schema.

The Meta template can stay in place — an unused approved template is harmless.

---

## 6. Clean up the verification artefacts

```sql
-- diagnostic dispatch rows created during the investigation
DELETE FROM notification_records
WHERE template_type = 'WHATSAPP_OPS_NOTIFICATION'
  AND payload->>'idempotency_key' IN (
    'wa_booking_notify:SHF-HARNESS-0001',
    'wa_booking_notify:SHF-HARNESS-0002',
    'wa_booking_notify:SHF-TEST-CLASS'
  );
```

Then cancel or delete the two diagnostic bookings in the admin portal:
`SHF-20261027-73036D8C` (2026-11-20) and `SHF-20261027-7C371712` (2026-11-21), both `PENDING`,
passenger "Shafsky Ops Test".

---

## 7. What "production ready" now means, precisely

| Concern | State after this change |
| --- | --- |
| Dispatch wired for every real creation path | yes — `/api/bookings`, `/api/bookings/enquiries`, `/api/airport/bookings`, ticketing, lounge, charter, journey query, bot flow |
| Five recipients, failure-isolated | yes — one rejected number never blocks the other four |
| Sender, credential and recipients correct | verified live: 5/5 accepted, then `DELIVERED` |
| Delivery outside the 24-hour window | **requires step 1** — code side is ready, Meta side is not |
| Meta failures diagnosable | yes — HTTP status, code, subcode, user message and remedy, per recipient, in logs and in `notification_records` |
| Duplicate suppression across restarts | yes — durable, database-backed, works even with Redis down |
| Duplicate suppression when two workers race the same event | only with Redis reachable — the database guard is check-then-act |
| Misconfiguration visible | yes — `ERROR` at startup naming the exact missing variable, plus `ERROR` per template-less send |
| Automatic retries | unchanged: `429` and `5xx` retried up to three times; a retried `5xx` can duplicate an alert (pre-existing, see report §8) |
