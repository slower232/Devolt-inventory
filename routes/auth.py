"""
routes/auth.py
Inicio de sesión, cierre de sesión y cambio de contraseña.
"""
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import models
from permisos import login_requerido

bp = Blueprint("auth", __name__)

LARGO_MINIMO_PASSWORD = 8


@bp.route("/login", methods=["GET", "POST"])
def login():
    # Si ya inició sesión, no tiene sentido mostrar el login otra vez.
    if "usuario_id" in session:
        return redirect(url_for("dashboard.inicio"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        usuario = models.obtener_usuario_por_username(username)

        # Mismo mensaje si el usuario no existe, está eliminado o la contraseña falla:
        # así no se revela qué usuarios existen.
        if (usuario is None or not usuario["activo"]
                or not check_password_hash(usuario["password_hash"], password)):
            flash("Usuario o contraseña incorrectos.", "error")
            return render_template("login.html"), 401

        # Sesión nueva: guardamos solo lo necesario para decidir permisos.
        session.clear()
        session["usuario_id"] = usuario["id"]
        session["nombre"] = usuario["nombre"]
        session["rol"] = usuario["rol"]
        session["equipo_id"] = usuario["equipo_id"]
        session["equipo_nombre"] = usuario["equipo_nombre"]
        session["debe_cambiar"] = bool(usuario["debe_cambiar_password"])

        if session["debe_cambiar"]:
            return redirect(url_for("auth.cambiar_password"))
        return redirect(url_for("dashboard.inicio"))

    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/cambiar-password", methods=["GET", "POST"])
@login_requerido
def cambiar_password():
    if request.method == "POST":
        actual = request.form.get("actual", "")
        nueva = request.form.get("nueva", "")
        confirmar = request.form.get("confirmar", "")

        usuario = models.obtener_usuario(session["usuario_id"])

        # Validaciones, una por una; en cuanto una falla se avisa y se detiene.
        if not check_password_hash(usuario["password_hash"], actual):
            flash("La contraseña actual no es correcta.", "error")
        elif len(nueva) < LARGO_MINIMO_PASSWORD:
            flash(f"La nueva contraseña debe tener al menos {LARGO_MINIMO_PASSWORD} caracteres.", "error")
        elif nueva != confirmar:
            flash("La confirmación no coincide con la nueva contraseña.", "error")
        elif nueva == actual:
            flash("La nueva contraseña debe ser distinta de la actual.", "error")
        else:
            models.cambiar_password(usuario["id"], nueva)
            session["debe_cambiar"] = False
            flash("Contraseña actualizada.", "ok")
            return redirect(url_for("dashboard.inicio"))

    return render_template("cambiar_password.html")
