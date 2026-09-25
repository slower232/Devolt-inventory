"""
routes/usuarios.py
Administracion de cuentas. Solo el Coach puede entrar a estas rutas.
"""
import secrets
import string

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import models
from permisos import rol_requerido

bp = Blueprint("usuarios", __name__)
LARGO_MINIMO_PASSWORD = 8


def _limpiar_texto(valor):
    return (valor or "").strip()


def _obtener_datos_formulario():
    """Lee y normaliza los campos del formulario."""
    nombre = _limpiar_texto(request.form.get("nombre"))
    username = _limpiar_texto(request.form.get("username"))
    password = request.form.get("password", "")
    rol = _limpiar_texto(request.form.get("rol")).lower()
    equipo_id_texto = _limpiar_texto(request.form.get("equipo_id"))
    return nombre, username, password, rol, equipo_id_texto


def _validar_datos(nombre, username, rol, equipo_id_texto, password=None, usuario_id=None):
    """Valida las reglas de negocio antes de escribir en SQLite."""
    errores = []
    equipo_id = None

    if not nombre:
        errores.append("El nombre es obligatorio.")
    if not username:
        errores.append("El nombre de usuario es obligatorio.")
    elif models.username_en_uso(username, usuario_id):
        errores.append("Ese nombre de usuario ya está en uso.")

    if rol not in ("alumno", "mentor", "coach"):
        errores.append("El rol seleccionado no es válido.")

    if rol == "coach":
        errores.append("No se puede crear ni asignar el rol de Coach desde este formulario.")
    elif rol == "alumno":
        if not equipo_id_texto:
            errores.append("Un alumno debe tener un equipo asignado.")
        else:
            try:
                equipo_id = int(equipo_id_texto)
            except ValueError:
                errores.append("El equipo seleccionado no es válido.")
            else:
                if not any(int(e["id"]) == equipo_id for e in models.obtener_equipos()):
                    errores.append("El equipo seleccionado no existe.")
    elif rol == "mentor":
        equipo_id = None

    if password is not None and len(password) < LARGO_MINIMO_PASSWORD:
        errores.append(
            f"La contraseña debe tener al menos {LARGO_MINIMO_PASSWORD} caracteres."
        )

    return errores, equipo_id


def _generar_password_temporal():
    """Genera una contraseña temporal fácil de copiar pero no predecible."""
    caracteres = string.ascii_letters + string.digits
    return "".join(secrets.choice(caracteres) for _ in range(12))


@bp.route("/usuarios")
@rol_requerido("coach")
def lista():
    return render_template("usuarios.html", usuarios=models.obtener_usuarios())


@bp.route("/usuarios/nuevo", methods=["GET", "POST"])
@rol_requerido("coach")
def nuevo():
    equipos = models.obtener_equipos()

    if request.method == "POST":
        nombre, username, password, rol, equipo_id_texto = _obtener_datos_formulario()
        errores, equipo_id = _validar_datos(
            nombre, username, rol, equipo_id_texto, password=password
        )

        if errores:
            for error in errores:
                flash(error, "error")
            return render_template("usuario_formulario.html", modo="nuevo", usuario=request.form,
                                   equipos=equipos)

        models.crear_usuario(nombre, username, password, rol, equipo_id)
        flash(f"Usuario '{username}' creado correctamente. Deberá cambiar su contraseña al iniciar sesión.", "ok")
        return redirect(url_for("usuarios.lista"))

    return render_template("usuario_formulario.html", modo="nuevo", usuario=None, equipos=equipos)


@bp.route("/usuarios/<int:usuario_id>/editar", methods=["GET", "POST"])
@rol_requerido("coach")
def editar(usuario_id):
    usuario = models.obtener_usuario_con_equipo(usuario_id)
    if usuario is None:
        return redirect(url_for("usuarios.lista"))

    equipos = models.obtener_equipos()

    if request.method == "POST":
        nombre, username, _password, rol, equipo_id_texto = _obtener_datos_formulario()
        activo = request.form.get("activo") == "1"
        errores, equipo_id = _validar_datos(
            nombre, username, rol, equipo_id_texto, usuario_id=usuario_id
        )

        # Una cuenta Coach existente puede conservar el rol Coach; no se permite
        # crear una cuenta nueva con ese rol desde este módulo.
        if usuario["rol"] == "coach" and rol == "coach":
            errores = [e for e in errores if not e.startswith("No se puede crear ni asignar el rol de Coach")]
            equipo_id = None

        # El Coach actual no puede quitarse su propio acceso ni cambiarse de rol.
        es_mismo_usuario = usuario_id == session.get("usuario_id")
        if es_mismo_usuario and not activo:
            errores.append("No puedes desactivar tu propia cuenta.")
        if es_mismo_usuario and rol != "coach":
            errores.append("No puedes cambiar tu propio rol de Coach.")

        # La interfaz no permite crear Coaches, pero protege también una edición
        # para que nunca se pueda dejar el sistema sin un Coach activo.
        if usuario["rol"] == "coach" and rol != "coach":
            errores.append("Las cuentas de Coach no pueden cambiarse a otro rol.")
        if usuario["rol"] == "coach" and not activo:
            errores.append("Las cuentas de Coach no se pueden desactivar.")

        if errores:
            for error in errores:
                flash(error, "error")
            usuario_form = dict(request.form)
            usuario_form["activo"] = "1" if activo else "0"
            return render_template("usuario_formulario.html", modo="editar", usuario=usuario_form,
                                   usuario_original=usuario, equipos=equipos)

        models.actualizar_usuario(usuario_id, nombre, username, rol, equipo_id, activo)
        flash(f"Usuario '{username}' actualizado correctamente.", "ok")
        return redirect(url_for("usuarios.lista"))

    return render_template("usuario_formulario.html", modo="editar", usuario=usuario,
                           usuario_original=usuario, equipos=equipos)


@bp.post("/usuarios/<int:usuario_id>/estado")
@rol_requerido("coach")
def cambiar_estado(usuario_id):
    usuario = models.obtener_usuario(usuario_id)
    if usuario is None:
        flash("El usuario no existe.", "error")
        return redirect(url_for("usuarios.lista"))

    if usuario_id == session.get("usuario_id"):
        flash("No puedes desactivar tu propia cuenta.", "error")
        return redirect(url_for("usuarios.lista"))

    # Solo se pueden desactivar alumnos y mentores; las cuentas de Coach no.
    if usuario["rol"] == "coach":
        flash("Las cuentas de Coach no se pueden desactivar.", "error")
        return redirect(url_for("usuarios.lista"))

    nuevo_estado = request.form.get("activo") == "1"

    models.cambiar_estado_usuario(usuario_id, nuevo_estado)
    flash("Cuenta reactivada." if nuevo_estado else "Cuenta desactivada.", "ok")
    return redirect(url_for("usuarios.lista"))


@bp.post("/usuarios/<int:usuario_id>/restablecer-password")
@rol_requerido("coach")
def restablecer_password(usuario_id):
    usuario = models.obtener_usuario(usuario_id)
    if usuario is None:
        flash("El usuario no existe.", "error")
        return redirect(url_for("usuarios.lista"))

    if usuario_id == session.get("usuario_id"):
        flash("Para tu propia cuenta utiliza 'Mi contraseña'.", "error")
        return redirect(url_for("usuarios.lista"))

    if not usuario["activo"]:
        flash("No puedes restablecer la contraseña de una cuenta desactivada.", "error")
        return redirect(url_for("usuarios.lista"))

    password_temporal = _generar_password_temporal()
    models.restablecer_password(usuario_id, password_temporal)
    return render_template(
        "password_temporal.html",
        usuario=usuario,
        password_temporal=password_temporal,
    )
