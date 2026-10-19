# Stock Club

**A mobile-first inventory web app for a sports club.** Stock Club helps volunteers catalogue equipment, organise it in a flexible category tree, attach photos, and track equipment loans and returns.

> The user interface is currently in French, for the club's volunteers. The code and this project overview are documented in English for a broader developer audience.

## Why this project?

Sports clubs often manage equipment through spreadsheets, paper lists, or informal messages. Stock Club brings the inventory into one shared, phone-friendly web app.

## Features

- **Flexible category tree:** create, rename, move, and organise branches at any depth.
- **Visual inventory:** equipment records include a photo, description, available quantity, condition, and storage location.
- **Photo upload:** validates and re-encodes supported images as WebP; EXIF metadata is removed during conversion.
- **Live search and filters:** search updates progressively while typing; category, availability, and minimum available quantity can be combined. Location and condition filters are intentionally omitted.
- **Borrow/return tracking:** each outing records the connected volunteer, the quantity initially taken out, and the outstanding quantity. Partial and complete returns are linked to their original outing; no expected return date is required.
- **Historical preservation on deletion:** only administrators can permanently delete an inventory item. Members see a disabled “Supprimer l’objet — Réservé aux administrateurs” action. Deletion is blocked while any quantity is still out; movement/loan history is kept with an item-name snapshot, and the photo object is deleted when no longer referenced.
- **Role-based access:** members manage the inventory; administrators can manage member accounts and issue one-use invitation links.
- **Mobile-friendly UI:** responsive layout and installable PWA manifest.
- **Storage separation:** SQL database for structured data and Neon Object Storage (S3-compatible) for photos in hosted deployments.
- **Database migrations and tests:** schema changes managed with Flask-Migrate/Alembic; automated tests included.

## Tech stack

- Python 3.12+
- Flask, Flask-Login, Flask-WTF
- Flask-SQLAlchemy and Flask-Migrate/Alembic
- PostgreSQL for shared hosted use; SQLite for local development
- Pillow for image validation and conversion
- boto3 for S3-compatible object storage
- Gunicorn for production serving
- HTML, CSS, JavaScript, and a lightweight PWA setup

## Architecture

```mermaid
flowchart TD
    A[Volunteer phone or browser] -->|HTTPS| B[Flask web application]
    B --> C[(PostgreSQL / Neon)]
    B --> D[(Private S3-compatible photo bucket)]
```

For local development, SQLite and a local uploads folder are used by default. For hosted use, use a managed PostgreSQL database and persistent object storage; do not rely on the web server's ephemeral filesystem for inventory data or photos.

## Run locally

Python 3.12 or a compatible Python 3.11+ version is recommended.

### 1. Create an environment and install dependencies

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Create local configuration

Windows PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
```

macOS/Linux:

```bash
cp .env.example .env
```

Set `SECRET_KEY` in `.env` to a random value. Generate one with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Never commit `.env` or any real credentials. `.env.example` contains placeholders only.

### 3. Initialise the database and create the first administrator

```bash
python -m flask --app run.py db upgrade
python -m flask --app run.py run --debug
```

Open `http://127.0.0.1:5000`. When the users table is empty, the login page offers **Create the first administrator**. After that account exists, the link is hidden and direct access to first-run setup is refused. For a terminal-only setup, `python -m flask --app run.py create-user` also makes the first account an administrator if the users table is empty; later CLI-created accounts are members. Never use an empty production database to test first-run setup after real users have been added. The `promote-admin` command is retained for controlled administrator recovery only.

The CLI prompts for a login and a password of at least six characters. On a fresh database, promote the first account to administrator with `python -m flask --app run.py promote-admin monlogin`. In an existing deployment, the role migration automatically promotes the oldest existing account. Administrators can create personal invitation links from **Membres**. Each link is single-use, expires after seven days, and creates a `Membre` account.

### 4. Start the development server

```bash
python -m flask --app run.py run --debug
```

Open <http://127.0.0.1:5000> in a browser.

## Run tests

```bash
python -m pytest -q
```

## Deployment

A sample Render Blueprint is included in `render.yaml`, and `DEPLOYMENT.md` describes a deployment using Render, Neon PostgreSQL, and Neon Object Storage. Version 2 adds migration `0005_admin_bootstrap`; take a Neon backup before deploying and let Render apply migrations using its configured start command. If the users table is empty, the login screen offers first-administrator setup. The final active administrator cannot be demoted.

Before deploying:

1. Configure a strong, private `SECRET_KEY` (Render can generate it through the Blueprint).
2. Set `DATABASE_URL` to your managed PostgreSQL connection string with TLS enabled.
3. Configure the Neon Object Storage endpoint, bucket, region, and credentials as environment variables in the hosting provider.
4. Keep the bucket private and use signed photo URLs unless a deliberate public-photo policy is chosen.
5. Configure and test database and photo backups, plus restoration.
6. Run the migration against the intended database before first use.

## Security and privacy

- Do not commit `.env`, database files, photo uploads, backup archives, access tokens, or passwords.
- Never place production secrets in source code, documentation, screenshots, or GitHub Actions logs.
- The repository contains configuration templates only; real deployment values belong in the hosting provider's secret/environment settings.
- The application has not undergone an independent security audit. Review the deployment configuration and backup/recovery process before relying on it for operational club data.

## Project status

Built iteratively around feedback from club-use testing. The app is designed for the club's current inventory workflow; future extensions could include individual asset tracking, QR labels, or loan due dates.

## License

No license has been added yet. Choose a license before granting others explicit permission to reuse, modify, or redistribute the code.
