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
