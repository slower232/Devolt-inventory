from flask import Blueprint, render_template, session

from models import obtener_resumen_inventario
from permisos import login_requerido

bp = Blueprint("dashboard", __name__)


@bp.route("/")
@login_requerido
def inicio():
    equipo_id = None if session.get("rol") in ("mentor", "coach") else session.get("equipo_id")
    resumen = obtener_resumen_inventario(equipo_id)
    return render_template("dashboard.html", resumen=resumen)
