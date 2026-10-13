from __future__ import annotations

import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import click
from dotenv import load_dotenv
from flask import Flask, abort, flash, redirect, render_template, send_from_directory, url_for
from flask_wtf.csrf import CSRFError
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix

from .extensions import csrf, db, login_manager, migrate


def _database_url() -> str:
    raw = os.environ.get("DATABASE_URL", "sqlite:///club_stock.db")
    if raw.startswith("postgres://"):
        raw = "postgresql+psycopg://" + raw[len("postgres://"):]
    elif raw.startswith("postgresql://"):
        raw = "postgresql+psycopg://" + raw[len("postgresql://"):]
    return raw


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    uploads = Path(app.instance_path) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "development-only-change-this-secret"),
        SQLALCHEMY_DATABASE_URI=_database_url(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
        UPLOAD_FOLDER=str(uploads),
        DEBUG=os.environ.get("FLASK_DEBUG", "0") == "1",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        S3_ENDPOINT_URL=os.environ.get("S3_ENDPOINT_URL"),
        S3_BUCKET=os.environ.get("S3_BUCKET"),
        S3_ACCESS_KEY_ID=os.environ.get("S3_ACCESS_KEY_ID"),
        S3_SECRET_ACCESS_KEY=os.environ.get("S3_SECRET_ACCESS_KEY"),
        S3_REGION=os.environ.get("S3_REGION", "auto"),
        S3_PUBLIC_BASE_URL=os.environ.get("S3_PUBLIC_BASE_URL", ""),
    )
    if test_config:
        app.config.update(test_config)

    secret = app.config.get("SECRET_KEY") or ""
    if not app.testing and not app.config.get("DEBUG") and (
        len(secret) < 32 or "replace-this" in secret or "change-this" in secret
    ):
        raise RuntimeError("Configure une SECRET_KEY aléatoire d’au moins 32 caractères avant de lancer l’application hors mode debug.")

    s3_keys = ("S3_BUCKET", "S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
    configured_s3 = [bool(app.config.get(key)) for key in s3_keys]
    if any(configured_s3) and not all(configured_s3):
        raise RuntimeError("Le stockage photo S3 est partiellement configuré : renseigne S3_BUCKET, S3_ENDPOINT_URL, S3_ACCESS_KEY_ID et S3_SECRET_ACCESS_KEY.")
    if os.environ.get("REQUIRE_OBJECT_STORAGE", "0") == "1" and not all(configured_s3):
        raise RuntimeError("REQUIRE_OBJECT_STORAGE=1 mais le stockage photo S3 n’est pas complètement configuré.")

    # When deployed behind a trusted reverse proxy (Render, etc.), honour HTTPS.
    if os.environ.get("TRUST_PROXY", "0") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Connecte-toi pour accéder à l’inventaire."
    login_manager.login_message_category = "info"

    from .models import Category, Item, StockMovement, User  # noqa: F401
    from .auth import bp as auth_bp
    from .inventory import bp as inventory_bp
    from .admin import bp as admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(admin_bp)

    @app.context_processor
    def inject_helpers():
        from .storage import photo_url
        return {"photo_url": photo_url}

    @app.template_filter("localdatetime")
    def local_datetime(value, fmt="%d/%m/%Y à %H:%M"):
        if value is None:
            return ""
        if value.tzinfo is None:
            from datetime import timezone
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(ZoneInfo("Europe/Paris")).strftime(fmt)

    @app.get("/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            app.logger.exception("Health check failed: database unavailable")
            return {"status": "unavailable"}, 503
        return {"status": "ok"}, 200

    @app.get("/sw.js")
    def service_worker():
        response = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        return render_template("error.html", code=400, message="Le formulaire a expiré. Recharge la page puis réessaie."), 400

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("error.html", code=403, message="Cette page est réservée aux administrateurs."), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message="Cette page n’existe pas ou n’est plus disponible."), 404

    @app.errorhandler(413)
    def file_too_large(error):
        return render_template("error.html", code=413, message="La photo dépasse la taille maximale de 8 Mo."), 413

    @app.cli.command("init-db")
    def init_db_command():
        """Create the initial database tables (safe to rerun for existing tables)."""
        with app.app_context():
            db.create_all()
        click.echo("Base de données initialisée.")

    @app.cli.command("create-user")
    @click.option("--login", "login_name", prompt="Nom de login")
    @click.password_option(confirmation_prompt=True, prompt="Mot de passe (6 caractères minimum)")
    def create_user_command(login_name, password):
        """Create a volunteer account (login + password; no email required)."""
        from .models import User
        login_name = login_name.strip().casefold()
        if not login_name or len(login_name) > 80 or any(ch.isspace() for ch in login_name) or "@" in login_name:
            raise click.ClickException("Le login doit contenir de 1 à 80 caractères, sans espace ni adresse e-mail.")
        if len(password) < 6:
            raise click.ClickException("Le mot de passe doit comporter au moins 6 caractères.")
        if User.query.filter_by(login=login_name).first():
            raise click.ClickException("Ce nom de login existe déjà.")
        user = User(name=login_name, login=login_name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Compte créé pour {login_name}.")

    @app.cli.command("set-user-password")
    @app.cli.command("set-user-pin")
    @click.option("--login", "login_name", prompt="Nom de login")
    @click.password_option(confirmation_prompt=True, prompt="Nouveau mot de passe (6 caractères minimum)")
    def set_user_pin_command(login_name, password):
        """Set/reset a volunteer's password without displaying it."""
        from .models import User
        login_name = login_name.strip().casefold()
        if len(password) < 6:
            raise click.ClickException("Le mot de passe doit comporter au moins 6 caractères.")
        user = User.query.filter_by(login=login_name).first()
        if user is None:
            raise click.ClickException("Utilisateur introuvable.")
        user.set_password(password)
        user.failed_login_attempts = 0
        user.locked_until = None
        db.session.commit()
        click.echo(f"Mot de passe mis à jour pour {user.login}.")

    @app.cli.command("promote-admin")
    @click.argument("login_name")
    def promote_admin_command(login_name):
        """Grant administrator access to an existing account (recovery/bootstrap)."""
        from .models import User
        user = User.query.filter_by(login=login_name.strip().casefold()).first()
        if user is None:
            raise click.ClickException("Utilisateur introuvable.")
        user.role = "admin"
        user.is_active_user = True
        db.session.commit()
        click.echo(f"Droits administrateur accordés à {user.login}.")

    @app.cli.command("deactivate-user")
    @click.argument("login_name")
    def deactivate_user_command(login_name):
        """Disable a volunteer account without deleting its history."""
        from .models import User
        user = User.query.filter_by(login=login_name.strip().casefold()).first()
        if user is None:
            raise click.ClickException("Utilisateur introuvable.")
        user.is_active_user = False
        db.session.commit()
        click.echo(f"Compte désactivé : {user.login}.")

    @app.cli.command("healthcheck")
    def healthcheck_command():
        """Check that the app can connect to its database."""
        with app.app_context():
            db.session.execute(text("SELECT 1"))
        click.echo("OK")

    return app
