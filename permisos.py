"""
permisos.py
Sistema de permisos. Un "decorador" es una función que se escribe encima de otra
(con @) para agregarle una verificación antes de que se ejecute.

Uso en las rutas:
    @login_requerido                       -> cualquier usuario con sesión
    @rol_requerido("mentor", "coach")      -> solo esos roles

Si no se cumple, el usuario es enviado al login (sin sesión) o ve un error 403 (sin permiso).
"""
from functools import wraps

from flask import abort, redirect, session, url_for


def login_requerido(funcion):
    @wraps(funcion)   # conserva el nombre original de la función (Flask lo necesita)
    def envoltura(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("auth.login"))
        return funcion(*args, **kwargs)
    return envoltura


def rol_requerido(*roles_permitidos):
    def decorador(funcion):
        @login_requerido              # primero se exige tener sesión
        @wraps(funcion)
        def envoltura(*args, **kwargs):
            if session.get("rol") not in roles_permitidos:
                abort(403)            # sesión válida, pero sin permiso para esta página
            return funcion(*args, **kwargs)
        return envoltura
    return decorador
