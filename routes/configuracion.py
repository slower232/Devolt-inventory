"""
routes/configuracion.py
Pantalla "Configuración": mentores y Coach agregan, renombran y eliminan
marcas, categorías y ubicaciones. Las tres funcionan igual, por eso comparten rutas:
la parte <tabla> de la dirección dice cuál se está usando.
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import models
from permisos import rol_requerido

bp = Blueprint("configuracion", __name__, url_prefix="/configuracion")

# tabla -> (título de la sección, nombre en singular para los mensajes)
SECCIONES = {
    "marcas": ("Marcas", "marca"),
    "categorias": ("Categorías", "categoría"),
    "ubicaciones": ("Ubicaciones", "ubicación"),
}
LARGO_MAXIMO = 60


def _validar_tabla(tabla):
    """Solo se aceptan las tres tablas conocidas; cualquier otra dirección da error 404."""
    if tabla not in SECCIONES:
        abort(404)


def _validar_nombre(tabla, nombre, item_id=None):
    """Devuelve un mensaje de error, o None si el nombre es válido."""
    if not nombre:
        return "El nombre no puede estar vacío."
    if len(nombre) > LARGO_MAXIMO:
        return f"El nombre es demasiado largo (máximo {LARGO_MAXIMO} caracteres)."
    if models.catalogo_nombre_repetido(tabla, nombre, excluir_id=item_id):
        return f"Ya existe una {SECCIONES[tabla][1]} llamada «{nombre}»."
    return None


@bp.route("/")
@rol_requerido("mentor", "coach")
def inicio():
    secciones = [
        {"tabla": tabla, "titulo": titulo, "singular": singular, "filas": models.catalogo_listar(tabla)}
        for tabla, (titulo, singular) in SECCIONES.items()
    ]
    return render_template("configuracion.html", secciones=secciones)


@bp.route("/<tabla>/agregar", methods=("POST",))
@rol_requerido("mentor", "coach")
def agregar(tabla):
    _validar_tabla(tabla)
    nombre = request.form.get("nombre", "").strip()
    error = _validar_nombre(tabla, nombre)
    if error:
        flash(error, "error")
    else:
        models.catalogo_agregar(tabla, nombre)
        flash(f"Se agregó la {SECCIONES[tabla][1]} «{nombre}».", "ok")
    return redirect(url_for("configuracion.inicio"))


@bp.route("/<tabla>/<int:item_id>/renombrar", methods=("POST",))
@rol_requerido("mentor", "coach")
def renombrar(tabla, item_id):
    _validar_tabla(tabla)
    nombre = request.form.get("nombre", "").strip()
    error = _validar_nombre(tabla, nombre, item_id)
    if error:
        flash(error, "error")
    else:
        models.catalogo_renombrar(tabla, item_id, nombre)
        flash(f"Nombre actualizado: «{nombre}».", "ok")
    return redirect(url_for("configuracion.inicio"))


@bp.route("/<tabla>/<int:item_id>/eliminar", methods=("POST",))
@rol_requerido("mentor", "coach")
def eliminar(tabla, item_id):
    _validar_tabla(tabla)
    try:
        models.catalogo_eliminar(tabla, item_id)
        flash(f"La {SECCIONES[tabla][1]} se eliminó.", "ok")
    except ValueError as exc:      # por ejemplo, todavía hay piezas que la usan
        flash(str(exc), "error")
    return redirect(url_for("configuracion.inicio"))
