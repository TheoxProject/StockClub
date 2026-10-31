from flask import Blueprint

bp = Blueprint("boxes", __name__, url_prefix="/boxes")

from . import routes  # noqa: E402,F401
