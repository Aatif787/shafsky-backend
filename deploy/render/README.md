# Deploy Shafsky backend on Render

Do these in order. Skipping Git or secrets will boot an image that is missing
the payment/auth fixes, or crash on fail-fast validation.

## 0. What Render already gives you

Render sets `PORT`, `RENDER=true`, and `RENDER_SERVICE_ID`. The app treats those
as a cloud runtime, so `ENVIRONMENT` **must** be `production` or `staging` or
startup raises. Do not rely on the Dockerfile default (`ENVIRONMENT` unset →
`development`).

Health check path: **`/ready`** (HTTP 200 when DB + Redis are up).

## 1. Commit and push the working tree

Render deploys from GitHub. Uncommitted files are **not** deployed.

Do **not** commit `.env`, `backups/*.enc`, `__pycache__`, or `.pytest_cache`.

From the repo root (PowerShell):

```powershell
git status
git add -A
git reset HEAD -- .env backups .pytest_cache
git status
```

Review the staged list. Then commit (only when you intend to) and push `main`.

After push, confirm GitHub shows the latest commit on `Aatif787/shafsky-backend`.

## 2. Apply the Blueprint (or create the services by hand)

`render.yaml` at the repo root defines:

| Resource | Purpose |
|----------|---------|
| `shafsky-backend` | Docker web service, health `/ready`, `alembic upgrade head` as pre-deploy |
| `shafsky-redis` | Redis-compatible Key Value (internal only) |
| Postgres | **Not created.** Paste your existing Neon `DATABASE_URL`. |

Dashboard: **New → Blueprint** → select this repo → fill every `sync: false`
variable when prompted.

If you create a Web Service manually instead:

1. Runtime: **Docker** (uses `Dockerfile`).
2. Health check: `/ready`.
3. Pre-deploy command: `alembic upgrade head`.
4. Create a **Key Value** instance and copy its internal `REDIS_URL`.
5. Set the env vars in the table below.

`RUN_MIGRATIONS` on the web service must stay **`false`**. Migrations run once
in pre-deploy so two instances do not race Alembic.

## 3. Required environment (production fail-fast)

| Variable | Value |
|----------|--------|
| `ENVIRONMENT` | `production` |
| `DATABASE_URL` | Neon URL (`postgres://` is rewritten to `postgresql://`; SSL is added in production) |
| `REDIS_URL` | From the Key Value service (internal `redis://...`) |
| `REQUIRE_REDIS` | `true` |
| `RUN_MIGRATIONS` | `false` |
| `TRUST_PROXY` | `true` |
| `COOKIE_SAMESITE` | `none` (Vercel HTTPS frontend + Render HTTPS API) |
| `ALLOWED_ORIGINS` | Exact frontend origins, comma-separated, e.g. `https://www.shafsky.com,https://shafsky.vercel.app` |
| `JWT_ALGORITHM` | `RS256` |
| `JWT_PRIVATE_KEY` | Full PEM (multiline in the dashboard, or a single line with `\n`) |
| `JWT_PUBLIC_KEY` | Matching public PEM |
| `JWT_REFRESH_SECRET` | ≥ 32 random characters |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` / `RAZORPAY_WEBHOOK_SECRET` | Live keys |
| `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_APP_SECRET` | Meta Cloud API |
| `RESEND_API_KEY` | Required at startup in production |

Recommended (not fail-fast, but needed for product):

`WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_WEBHOOK_VERIFY_TOKEN`, `EMAIL_FROM`,
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (invoices — Render disk is ephemeral).

Keep `ALLOW_ADMIN_BOOTSTRAP=false` and `ALLOW_HS256_LEGACY_FALLBACK=false`.

`DB_POOL_SIZE=5` and `DB_MAX_OVERFLOW=5` keep Neon connection limits in check
with `WEB_CONCURRENCY=2`.

## 4. Generate JWT PEMs if you do not already have a pair

```powershell
python -c "from cryptography.hazmat.primitives.asymmetric import rsa; from cryptography.hazmat.primitives import serialization; k=rsa.generate_private_key(public_exponent=65537,key_size=2048); print(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()); print(k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())"
```

Paste both blocks into Render. Changing keys invalidates existing access tokens.

## 5. Point webhooks at the Render URL

After the first successful deploy, note `https://<service>.onrender.com`.

- Razorpay dashboard → Webhooks → `https://<service>.onrender.com/api/payments/razorpay/webhook`
- Meta WhatsApp → Callback URL `https://<service>.onrender.com/api/whatsapp/webhook`
- Razorpay **website allowlist** must include the frontend origin that hosts Checkout, not only the API host.

## 6. Smoke after deploy

```bash
curl -fsS https://<service>.onrender.com/live
curl -fsS https://<service>.onrender.com/ready
```

`/ready` must be **200**. If it is **503**, logs will show DB or Redis. If the
process never starts, logs will show `CRITICAL SECURITY FAIL-FAST` for a missing
secret or `ENVIRONMENT must be explicitly set`.

## 7. Do not do these

- Do not set `ENVIRONMENT=development` on Render (boot is refused).
- Do not set `RUN_MIGRATIONS=true` on the web service if you scale above 1.
- Do not create a second Postgres and expect Neon data to appear there.
- Do not store invoice PDFs only on the container filesystem.
