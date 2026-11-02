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

## Create the first administrator

Run this in the project root. The password prompt is hidden and requires
confirmation; passwords must be at least 12 characters.

```powershell
flask --app app create-admin
```

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
change the password before accessing school pages.

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
vendored under `Static/vendor/` with its MIT license; it is not loaded from a
third-party CDN.

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
