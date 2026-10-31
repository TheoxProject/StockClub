from functools import wraps

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user
from sqlalchemy import text, update
from sqlalchemy.exc import OperationalError

from .extensions import db
from .models import BootstrapLock, User


def acquire_lock():
    """Serialize cooperating mutations until commit/rollback on SQLite and PostgreSQL."""
    if db.session.get_bind().dialect.name == "postgresql":
        db.session.execute(text("SET LOCAL lock_timeout = '5s'"))
    result = db.session.execute(update(BootstrapLock).where(BootstrapLock.id == 1).values(lock_version=BootstrapLock.lock_version))
    if result.rowcount != 1:
        raise RuntimeError("Verrou transactionnel absent : applique les migrations.")


def synchronized(*, admin=False):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if request.method in {"GET", "HEAD", "OPTIONS"}:
                return view(*args, **kwargs)
            try:
                acquire_lock()
                caller = User.query.filter_by(id=current_user.id).populate_existing().first()
                if caller is None or not caller.is_active_user or (admin and not caller.is_admin):
                    abort(403)
                return view(*args, **kwargs)
            except OperationalError as exc:
                db.session.rollback()
                orig = exc.orig
                code = getattr(orig, 'sqlstate', None) or getattr(orig, 'pgcode', None)
                message = str(orig).lower()
                if code not in {'40001', '40P01', '55P03'} and not any(s in message for s in ('database is locked', 'database table is locked', 'deadlock', 'serialization failure')):
                    raise
                flash("Une opération concurrente est en cours. Réessaie dans un instant.", "error")
                return redirect(url_for('admin.members' if admin else 'inventory.index'))
        return wrapped
    return decorate
