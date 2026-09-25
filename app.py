"""
app.py
Punto de entrada: crea la aplicación Flask, la conecta con la base de datos
y registra las rutas. Para ejecutar el proyecto:  python app.py
"""
import os
import secrets
from datetime import datetime

from flask import Flask, flash, redirect, render_template, request, session, url_for

import database
import models
from routes import auth, configuracion, dashboard, historial, inventario, usuarios


def obtener_clave_secreta():
    """Flask firma las sesiones con una clave secreta. Se lee de la variable de entorno
    DEVOLT_SECRET_KEY o, si no existe, se genera una vez y se guarda en database/secret.key
    (así las sesiones no se pierden al reiniciar y la clave no queda escrita en el código)."""
    clave = os.environ.get("DEVOLT_SECRET_KEY")
    if clave:
        return clave
    ruta = os.path.join(database.CARPETA_BASE, "database", "secret.key")
    if not os.path.exists(ruta):
        with open(ruta, "w") as archivo:
            archivo.write(secrets.token_hex(32))
    with open(ruta) as archivo:
        return archivo.read().strip()


database.iniciar_db()   # crea tablas y datos iniciales si hace falta

app = Flask(__name__)
app.secret_key = obtener_clave_secreta()
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024   # las fotos de piezas no pueden pasar de 4 MB
app.teardown_appcontext(database.cerrar_db)          # cierra la conexión al terminar cada petición

app.register_blueprint(auth.bp)
app.register_blueprint(dashboard.bp)
app.register_blueprint(usuarios.bp)
app.register_blueprint(inventario.bp)
app.register_blueprint(historial.bp)
app.register_blueprint(configuracion.bp)


@app.template_global()
def imagen_url(nombre):
    """Dirección de la foto de una pieza para usar en las plantillas: {{ imagen_url(pieza.imagen) }}.
    Normalmente es un archivo subido (carpeta static/images/piezas); si la pieza se guardó
    antes con un enlace externo, ese enlace también se sigue mostrando."""
    if not nombre:
        return None
    if nombre.startswith(("http://", "https://")):
        return nombre
    return url_for("static", filename="images/piezas/" + nombre)


@app.template_filter("fecha")
def formato_fecha(valor):
    """Muestra las fechas de la base de datos como dd/mm/aaaa hh:mm: {{ algo.fecha|fecha }}"""
    if not valor:
        return ""
    try:
        return datetime.strptime(valor, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return valor


@app.before_request
def revisar_sesion():
    """Se ejecuta antes de CADA página. Vuelve a leer al usuario en la base de datos para que:
      - una cuenta desactivada pierda el acceso de inmediato (aunque tenga la sesión abierta),
      - un cambio de rol o de equipo hecho por el Coach se aplique sin esperar a otro login,
      - una contraseña temporal obligue a cambiarla antes de usar cualquier otra página."""
    if request.endpoint == "static" or "usuario_id" not in session:
        return None

    usuario = models.obtener_usuario_con_equipo(session["usuario_id"])
    if usuario is None or not usuario["activo"]:
        session.clear()
        flash("Tu cuenta está desactivada. Habla con el Coach.", "error")
        return redirect(url_for("auth.login"))

    session["nombre"] = usuario["nombre"]
    session["rol"] = usuario["rol"]
    session["equipo_id"] = usuario["equipo_id"]
    session["equipo_nombre"] = usuario["equipo_nombre"]
    session["debe_cambiar"] = bool(usuario["debe_cambiar_password"])

    paginas_libres = ("auth.cambiar_password", "auth.logout")
    if session["debe_cambiar"] and request.endpoint not in paginas_libres:
        return redirect(url_for("auth.cambiar_password"))
    return None


@app.errorhandler(403)
def sin_permiso(error):
    return render_template("error.html", titulo="Sin permiso",
                           mensaje="Tu rol no puede entrar a esta página."), 403


@app.errorhandler(404)
def no_encontrado(error):
    return render_template("error.html", titulo="Página no encontrada",
                           mensaje="La dirección no existe. Vuelve al inicio."), 404


@app.errorhandler(413)
def archivo_muy_grande(error):
    return render_template("error.html", titulo="Archivo demasiado grande",
                           mensaje="La imagen no puede pasar de 4 MB. Elige una más ligera."), 413


if __name__ == "__main__":
    # Modo normal: host="0.0.0.0" permite entrar desde otras computadoras de la red local.
    # Modo desarrollo (variable de entorno DEVOLT_DEBUG=1): recarga al guardar cambios y
    # muestra errores detallados, por eso SOLO acepta conexiones de esta computadora.
    if os.environ.get("DEVOLT_DEBUG") == "1":
        app.run(host="127.0.0.1", port=5000, debug=True)
    else:
        app.run(host="0.0.0.0", port=5000)
