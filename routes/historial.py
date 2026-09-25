"""Historial de operaciones del inventario."""
from flask import Blueprint, render_template, request, session

import models
from permisos import rol_requerido

bp = Blueprint("historial", __name__, url_prefix="/historial")

TIPOS = {
    "pieza_nueva": "Pieza nueva",
    "unidades_agregadas": "Unidades agregadas",
    "solicitud": "Solicitud de piezas",
    "aprobacion": "Solicitud aprobada",
    "rechazo": "Solicitud rechazada",
    "devolucion": "Devolución al stock",
    "solicitud_compra": "Solicitud de compra",
    "compra_aprobada": "Compra aprobada",
    "compra_rechazada": "Compra rechazada",
    "compra_realizada": "Compra realizada",
    "compra_recibida": "Compra recibida",
    "retiro_danado": "Pieza retirada por daño",
    "pieza_editada": "Ficha editada",
}


@bp.route("/")
@rol_requerido("mentor", "coach")
def lista():
    filtros = {
        "q": request.args.get("q", ""),
        "tipo": request.args.get("tipo", ""),
        "equipo_id": request.args.get("equipo_id", ""),
        "usuario_id": request.args.get("usuario_id", ""),
    }
    return render_template(
        "historial.html",
        historial=models.obtener_historial(filtros),
        filtros=filtros,
        equipos=models.obtener_equipos(),
        usuarios=models.obtener_usuarios(),
        tipos=models.obtener_tipos_historial(),
        nombres_tipo=TIPOS,
    )
