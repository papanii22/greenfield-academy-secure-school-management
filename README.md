# Greenfield Academy Secure School Management System

Stage 1 establishes the Flask application, configuration, PostgreSQL schema, and
dependency list. The database connection check is run after the Supabase project
and local `.env` file have been configured.

## Local setup (Windows PowerShell)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in `.env` using the setup instructions provided at the Stage 1 stop point.
Do not commit `.env`; `.gitignore` excludes it.

## Run the application

```powershell
flask --app app run
```

The login screen is available at `http://127.0.0.1:5000/login`. The app uses
PostgreSQL and requires a configured `.env` file.

## Deploy to Vercel

Vercel detects the Flask application in `app.py` and installs dependencies
from `requirements.txt`; no custom build or start command is needed. The
application's browser assets are in `public/Static/`, which Vercel serves at
the existing `/Static/...` URLs. The Flask static route also serves these files
for local development and Render.

Import the private GitHub repository into Vercel with the repository root as
the project root. In **Project Settings > Environment Variables**, add these
for each environment you deploy (Production, and Preview if needed):

- `SECRET_KEY` — keep the same key used to encrypt MFA data in the connected
  database. Changing it can prevent already-enrolled users from authenticating.
- `DATABASE_URL` — the PostgreSQL connection URL. Use a hosted database that
  accepts connections from Vercel, with SSL enabled.
- `JWT_SECRET` — a strong, independently generated secret.
- `FLASK_DEBUG` = `false`
- `SESSION_COOKIE_SECURE` = `true`
- `TRUST_PROXY_HEADERS` = `true`
- `RATELIMIT_STORAGE_URI` = `memory://` for a single-instance demo only.
- `RATELIMIT_ENABLED` = `true`
- `CURRENT_TERM` = `1` (optional; the application defaults to `1`).

The three secrets are required at application startup. Vercel does not read
your local `.env`; enter values directly in Vercel and never commit or share
`.env`. Redeploy after changing environment variables. Check the deployment's
**Runtime Logs** if it returns an error. Before production or multiple
instances, configure shared Redis-compatible rate-limit storage and validate
database connection limits. Use fictional/demo records on free tiers, not real
student data.

## Deploy to Render

The repository includes `render.yaml` for a free Render demo web service in
the Frankfurt region. Free services can sleep when idle and cold-start on the
next request; use this tier only with fictional/demo data, not real student
records. Push the project to a private GitHub repository, then create a
Blueprint in Render connected to that repository. Enter the current
`SECRET_KEY` from `.env`, a newly generated
`JWT_SECRET`, and the rotated Supabase `DATABASE_URL` as secret environment
variables in the Render dashboard. Use the same `SECRET_KEY` that encrypted
existing MFA data; if it was changed after enrollment, MFA resets are required.
Never commit `.env` or paste secret values into chat.

The service uses Gunicorn, HTTPS-only session cookies, and one trusted proxy
hop for client IP handling. Its `/healthz` route supports Render health checks.
Do not change the `SECRET_KEY` after deployment: MFA secrets are encrypted
using it. If you already changed that key, incomplete MFA enrollment can be
restarted from the setup page; already-enabled MFA accounts need a careful
administrative reset before they can authenticate again.

The Blueprint uses an in-memory rate-limit store and one worker for the initial
single-instance deployment. Before adding workers or scaling to multiple
instances, provision shared Redis-compatible storage and set
`RATELIMIT_STORAGE_URI` to its private internal URL in Render.

## Create the first administrator

Run this in the project root. The password prompt is hidden and requires
confirmation; passwords must be at least 12 characters.

```powershell
flask --app app create-admin
```

On Render, open the service Shell and run the same command there to create the
initial administrator against the hosted database.

## Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q Tests
```

Authentication uses Argon2 password hashes, parameterized PostgreSQL queries,
CSRF-protected POST forms, session clearing on successful login, and audit
events for successful and failed logins, logout, password changes, and
administrator creation. New accounts receive a one-time temporary password and
must change it before accessing their dashboard.

## Mandatory MFA and read-only API

MFA is required for every role. After entering a valid password, users enroll an
authenticator app by scanning the locally generated QR code, then confirming a
time-based verification code. Ten one-time recovery codes are shown once after
enrollment; store them securely. The authenticator secret is encrypted in the
database, and recovery codes are stored as password hashes. Login is not
complete until MFA succeeds. Accounts with temporary passwords must still
change the password before accessing school pages. While initial setup is
pending, the setup page can generate a fresh authenticator key if the current
one was exposed or cannot be verified; this does not reset already-enabled MFA.

The login, MFA, password-change, and API-token forms are rate limited. The
default `RATELIMIT_STORAGE_URI=memory://` is suitable for local development
only; configure shared persistent storage such as Redis when deploying multiple
workers. Set `RATELIMIT_STORAGE_URI` in `.env` to the storage URI for that
service.

An MFA-verified user can create a bearer token from the authenticated
application. Tokens are signed with `JWT_SECRET`, expire after 15 minutes, and
grant only `profile:read` and `dashboard:read` scopes. Keep this secret private
and use a strong, independently generated value. The read-only endpoints are:

- `GET /api/v1/me` — the authenticated user's profile.
- `GET /api/v1/dashboard` — the authenticated user's role-scoped dashboard;
  optionally pass `?term=1`, `?term=2`, or `?term=3`.

Send tokens in the `Authorization: Bearer <token>` header. API responses are
marked `no-store`; endpoints also check that the account is still active and
the role still matches the token. Do not put bearer tokens in URLs or logs.

`database/rls_proposal.sql` is documentation only and must not be executed.
The current application uses its own Argon2 authentication and database
connection, not Supabase Auth identities. Real Supabase row-level security
requires an identity mapping and verified non-owner database role first; the
proposal documents these prerequisites but does not enable policies.

The interface uses local CSS and JavaScript assets only. Chart.js 4.6.1 is
vendored under `public/Static/vendor/` with its MIT license; it is not loaded
from a third-party CDN.

The dashboard default term is controlled by the optional `CURRENT_TERM`
setting (`1`, `2`, or `3`); term filters remain available in each dashboard.

Administrators can export school-wide attendance, results, grade distributions,
and filtered audit activity as CSV or PDF. Teacher exports are limited to their
assigned class, date, subject, and term; student exports contain only the
signed-in student's attendance or results. CSV downloads protect spreadsheet
cells from formula execution, downloads are marked `no-store`, and exports are
capped at 5,000 rows. Administrator attendance and audit exports default to the
most recent 30 days. If a name or other value contains a character unsupported
by the bundled PDF font, the PDF download explains that CSV should be used
instead rather than silently altering the data.
