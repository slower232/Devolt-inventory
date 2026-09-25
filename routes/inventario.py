"""
routes/inventario.py
Inventario: lista, detalle, alta y edición de piezas, agregar unidades, imágenes,
y (por ahora) las solicitudes de piezas. Las solicitudes se mudarán a su propio
archivo cuando agreguemos las devoluciones.
"""
import json
import os
import re
import uuid
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

import models
import reglas
from database import CARPETA_BASE
from permisos import login_requerido, rol_requerido

bp = Blueprint("inventario", __name__, url_prefix="/inventario")

CARPETA_IMAGENES = os.path.join(CARPETA_BASE, "static", "images", "piezas")
EXTENSIONES_IMAGEN = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
# Los archivos que guardamos siempre se llaman así (32 letras/números + extensión).
PATRON_NOMBRE_IMAGEN = re.compile(r"^[0-9a-f]{32}\.(png|jpg|jpeg|webp|gif)$")


# ---------------------------------------------------------------------------
# Funciones auxiliares
# ---------------------------------------------------------------------------
def _es_personal():
    """True si quien navega es mentor o Coach (los alumnos tienen menos permisos)."""
    return session["rol"] in ("mentor", "coach")


def _entero_o_none(texto):
    """Convierte "3" en 3. Si viene vacío o no es número, devuelve None."""
    try:
        return int(texto)
    except (TypeError, ValueError):
        return None


def _es_url_http(texto):
    """Solo se aceptan enlaces http/https (un enlace "javascript:..." sería peligroso)."""
    partes = urlparse(texto)
    return partes.scheme in ("http", "https") and bool(partes.netloc)


def _normalizar_url_importacion(texto):
    """Limpia el enlace pegado y limita la importación automática a goBILDA."""
    texto = (texto or "").replace("\u200b", "").replace("\ufeff", "").strip().strip('\"\'<>')
    if texto.startswith("www.gobilda.com/"):
        texto = "https://" + texto
    elif texto.startswith("gobilda.com/"):
        texto = "https://www." + texto

    if not _es_url_http(texto):
        raise ValueError("Pega un enlace válido de goBILDA, por ejemplo https://www.gobilda.com/...")

    partes = urlparse(texto)
    host = (partes.hostname or "").lower().rstrip(".")
    if not (host == "gobilda.com" or host.endswith(".gobilda.com")):
        raise ValueError("La importación automática está habilitada por ahora para enlaces de goBILDA.")
    return texto


def _meta(soup, *selectores):
    for selector in selectores:
        nodo = soup.select_one(selector)
        if nodo:
            valor = nodo.get("content") or nodo.get_text(" ", strip=True)
            if valor:
                return valor.strip()
    return ""


def _precio_de_jsonld(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        objetos = data if isinstance(data, list) else [data]
        for objeto in objetos:
            if not isinstance(objeto, dict):
                continue
            offers = objeto.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else None
            if isinstance(offers, dict):
                precio = offers.get("price")
                try:
                    return float(str(precio).replace(",", ""))
                except (TypeError, ValueError):
                    pass
    return None


def _categoria_importada(nombre, url, categorias):
    texto = f"{nombre} {url}".lower()
    reglas_categoria = [
        ("Motores", ("motor", "gear motor", "planetary gear motor")),
        ("Servos", ("servo",)),
        ("Engranes", ("gear", "gearbox", "pinion", "spur gear", "bevel gear")),
        ("Standoffs", ("standoff", "spacer")),
        ("Quadblocks", ("quad block", "quadblock", "pattern mount")),
        ("Poleas", ("pulley", "timing belt", "round belt")),
        ("Ruedas", ("wheel", "tire")),
        ("Placas", ("plate", "channel", "beam", "bracket", "mount")),
        ("Electrónica", ("sensor", "encoder", "control hub", "camera", "electronics", "motor controller")),
    ]
    nombre_categoria = "Otros"
    for candidata, claves in reglas_categoria:
        if any(clave in texto for clave in claves):
            nombre_categoria = candidata
            break
    return next((c["id"] for c in categorias if c["nombre"].lower() == nombre_categoria.lower()), None)


def _importar_producto_gobilda(url_producto, listas):
    """Obtiene los campos básicos de una página de producto de goBILDA."""
    url_producto = _normalizar_url_importacion(url_producto)
    try:
        respuesta = requests.get(
            url_producto,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DevoltInventory/1.0",
                "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
            },
            timeout=12,
        )
        respuesta.raise_for_status()
    except requests.RequestException as exc:
        raise ValueError(f"No se pudo abrir la página de goBILDA: {exc}") from exc

    soup = BeautifulSoup(respuesta.text, "html.parser")
    texto = soup.get_text(" ", strip=True)

    nombre = _meta(soup, 'meta[property="og:title"]', 'meta[name="twitter:title"]')
    h1 = soup.find("h1")
    if h1:
        nombre = h1.get_text(" ", strip=True) or nombre
    nombre = re.sub(r"\s+", " ", nombre).strip()
    if not nombre:
        raise ValueError("No se pudo leer el nombre del producto desde la página.")

    sku_match = re.search(r"\bSKU\s*:\s*([A-Za-z0-9._-]+)", texto, flags=re.IGNORECASE)
    sku = sku_match.group(1).strip() if sku_match else ""

    precio = _precio_de_jsonld(soup)
    if precio is None:
        precio_match = re.search(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", texto)
        if precio_match:
            try:
                precio = float(precio_match.group(1).replace(",", ""))
            except ValueError:
                precio = 0.0
    if precio is None:
        precio = 0.0

    imagen = _meta(
        soup,
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
    )
    if imagen:
        imagen = urljoin(url_producto, imagen)

    marca_id = next((m["id"] for m in listas["marcas"] if m["nombre"].lower() == "gobilda"), None)
    categoria_id = _categoria_importada(nombre, url_producto, listas["categorias"])

    datos = {
        "nombre": nombre[:120],
        "marca_id": marca_id,
        "sku": sku[:60],
        "categoria_id": categoria_id,
        "costo_unitario": precio,
        "link": url_producto,
        "imagen": imagen or None,
        "cantidad_total": 1,
        "equipo_id": None,
        "ubicacion_id": None,
    }
    if not datos["sku"]:
        raise ValueError("La página abrió correctamente, pero no se encontró el SKU del producto.")
    if models.sku_en_uso(datos["sku"]):
        raise ValueError(f"El SKU {datos['sku']} ya existe en tu inventario.")
    return datos


def _guardar_imagen(archivo):
    """Guarda la imagen subida con un nombre aleatorio y devuelve ese nombre.
    Devuelve None si no se subió ningún archivo."""
    if archivo is None or archivo.filename == "":
        return None
    extension = os.path.splitext(archivo.filename)[1].lower()
    if extension not in EXTENSIONES_IMAGEN:
        raise ValueError("La imagen debe ser PNG, JPG, WEBP o GIF.")
    os.makedirs(CARPETA_IMAGENES, exist_ok=True)
    nombre = uuid.uuid4().hex + extension
    archivo.save(os.path.join(CARPETA_IMAGENES, nombre))
    return nombre


def _borrar_imagen(nombre):
    """Borra una imagen guardada por nosotros. Ignora enlaces externos y cualquier
    nombre que no tenga el formato esperado (así nunca se borra otro archivo por error)."""
    if nombre and PATRON_NOMBRE_IMAGEN.match(nombre):
        try:
            os.remove(os.path.join(CARPETA_IMAGENES, nombre))
        except OSError:
            pass


def _cargar_listas():
    """Las listas que llenan los <select> de los formularios."""
    return {
        "marcas": models.obtener_marcas(),
        "categorias": models.obtener_categorias(),
        "ubicaciones": models.obtener_ubicaciones(),
        "equipos": models.obtener_equipos(),
    }


def _leer_formulario(form, es_personal, listas, con_stock):
    """Lee y valida el formulario de una pieza. Devuelve (datos, errores).
    con_stock=True incluye cantidad inicial y asignación inicial (solo al crear)."""
    errores = []
    datos = {
        "nombre": form.get("nombre", "").strip(),
        "marca_id": _entero_o_none(form.get("marca_id")),
        "sku": form.get("sku", "").strip(),
        "categoria_id": _entero_o_none(form.get("categoria_id")),
        "costo_unitario": form.get("costo_unitario", "").strip(),
        "link": form.get("link", "").strip(),
        "ubicacion_id": None,
        "cantidad_total": 0,
        "equipo_id": None,
    }

    if not datos["nombre"]:
        errores.append("El nombre es obligatorio.")
    elif len(datos["nombre"]) > 120:
        errores.append("El nombre es demasiado largo (máximo 120 caracteres).")

    if datos["marca_id"] is not None and datos["marca_id"] not in {m["id"] for m in listas["marcas"]}:
        errores.append("La marca seleccionada no existe.")
    if datos["categoria_id"] not in {c["id"] for c in listas["categorias"]}:
        errores.append("Selecciona una categoría.")

    try:
        costo = float(datos["costo_unitario"] or 0)
        if costo < 0:
            raise ValueError
        datos["costo_unitario"] = costo
    except ValueError:
        errores.append("El costo unitario debe ser un número mayor o igual a cero.")

    if datos["link"] and not _es_url_http(datos["link"]):
        errores.append("El enlace del producto debe empezar con http:// o https://")
    if es_personal:
        # La ubicación la fijan mentores y Coach (el alumno solo crea la ficha).
        datos["ubicacion_id"] = _entero_o_none(form.get("ubicacion_id"))
        if datos["ubicacion_id"] not in {u["id"] for u in listas["ubicaciones"]}:
            errores.append("Selecciona una ubicación.")
        if con_stock:
            try:
                cantidad = int(form.get("cantidad_total", "0") or 0)
                if cantidad < 0:
                    raise ValueError
                datos["cantidad_total"] = cantidad
            except ValueError:
                errores.append("La cantidad debe ser un número entero mayor o igual a cero.")
            datos["equipo_id"] = _entero_o_none(form.get("equipo_id"))
            if datos["equipo_id"] is not None and datos["equipo_id"] not in {e["id"] for e in listas["equipos"]}:
                errores.append("El equipo de la asignación inicial no existe.")
    return datos, errores


def _mostrar_formulario(modo, datos, errores, action, pieza=None, importada=False):
    return render_template(
        "pieza_formulario.html", modo=modo, datos=datos, errores=errores, action=action,
        pieza=pieza, es_personal=_es_personal(), importada=importada, **_cargar_listas(),
    ), (400 if errores else 200)


# ---------------------------------------------------------------------------
# Lista y detalle
# ---------------------------------------------------------------------------
@bp.route("/")
@login_requerido
def lista():
    filtros = {
        "q": request.args.get("q", ""),
        "marca_id": request.args.get("marca_id", ""),
        "categoria_id": request.args.get("categoria_id", ""),
        "ubicacion_id": request.args.get("ubicacion_id", ""),
        "disponibilidad": request.args.get("disponibilidad", ""),
    }
    return render_template(
        "inventario.html", piezas=models.obtener_inventario(filtros), filtros=filtros, **_cargar_listas()
    )


@bp.route("/importar-link", methods=("GET", "POST"))
@login_requerido
def importar_link():
    if request.method == "GET":
        return render_template("importar_link.html", enlace="", errores=[])

    enlace = request.form.get("enlace", "")
    try:
        datos = _importar_producto_gobilda(enlace, _cargar_listas())
    except ValueError as exc:
        return render_template("importar_link.html", enlace=enlace, errores=[str(exc)]), 400

    return render_template(
        "pieza_formulario.html",
        modo="nuevo",
        datos=datos,
        errores=[],
        action=url_for("inventario.nuevo"),
        pieza=None,
        es_personal=_es_personal(),
        importada=True,
        **_cargar_listas(),
    )


@bp.route("/<int:pieza_id>")
@login_requerido
def detalle(pieza_id):
    pieza = models.obtener_pieza(pieza_id)
    if pieza is None or not pieza["activa"]:
        return render_template("error.html", titulo="Pieza no encontrada",
                               mensaje="La pieza no existe o ya no está activa."), 404

    asignaciones = models.obtener_asignaciones_pieza(pieza_id)
    if not _es_personal():
        # Regla 2: el alumno solo ve a su propio equipo (y el stock comunitario).
        asignaciones = [a for a in asignaciones if a["id"] == session["equipo_id"]]
    return render_template("pieza_detalle.html", pieza=pieza, asignaciones=asignaciones,
                           es_personal=_es_personal(), equipos=models.obtener_equipos())


# ---------------------------------------------------------------------------
# Alta de piezas (cualquier usuario puede crear la ficha; solo mentores/Coach fijan cantidad)
# ---------------------------------------------------------------------------
@bp.route("/nuevo", methods=("GET", "POST"))
@login_requerido
def nuevo():
    es_personal = _es_personal()
    accion = url_for("inventario.nuevo")

    if request.method == "GET":
        return _mostrar_formulario("nuevo", {}, [], accion)

    listas = _cargar_listas()
    importada = request.form.get("imagen_importada") == "1"
    datos, errores = _leer_formulario(request.form, es_personal, listas, con_stock=True)

    # Cuando un alumno registra una pieza nueva, las unidades son piezas que
    # ya tiene físicamente su equipo. Nunca puede elegir otro equipo.
    if not es_personal:
        cantidad = _entero_o_none(request.form.get("cantidad_total"))
        if cantidad is None or cantidad <= 0:
            errores.append("Indica cuántas unidades tiene actualmente tu equipo (mínimo 1).")
        else:
            datos["cantidad_total"] = cantidad
            datos["equipo_id"] = session.get("equipo_id")
            if datos["equipo_id"] is None:
                errores.append("Tu usuario no tiene un equipo asignado.")

    # Imagen: puede ser un archivo local o, cuando se importó desde goBILDA,
    # la URL pública de la imagen del producto.
    imagen_nueva = None
    if not errores:
        try:
            imagen_nueva = _guardar_imagen(request.files.get("imagen_archivo"))
        except ValueError as exc:
            errores.append(str(exc))
    if imagen_nueva:
        datos["imagen"] = imagen_nueva
    else:
        imagen_externa = (request.form.get("imagen_externa") or "").strip()
        imagen_importada = request.form.get("imagen_importada") == "1"
        if imagen_externa and _es_url_http(imagen_externa):
            host_imagen = (urlparse(imagen_externa).hostname or "").lower()
            es_gobilda = host_imagen == "gobilda.com" or host_imagen.endswith(".gobilda.com")
            # Muchas imágenes de goBILDA se sirven desde un CDN externo.
            # Si vienen de la pantalla de importación, confiamos en que fueron
            # obtenidas desde la página de goBILDA y permitimos conservarlas.
            if es_gobilda or imagen_importada:
                datos["imagen"] = imagen_externa
            else:
                errores.append("La imagen debe ser un enlace válido o provenir de la importación de goBILDA.")
        elif imagen_externa:
            errores.append("La imagen debe tener un enlace http:// o https:// válido.")
        else:
            datos["imagen"] = None

    if not errores:
        try:
            reglas.crear_pieza(datos, session["usuario_id"])
        except ValueError as exc:
            errores.append(str(exc))
            _borrar_imagen(imagen_nueva)          # no dejar archivos huérfanos
        else:
            flash("La pieza se agregó al inventario.", "ok")
            return redirect(url_for("inventario.lista"))

    return _mostrar_formulario("nuevo", datos, errores, accion, importada=importada)


# ---------------------------------------------------------------------------
# Edición de la ficha y agregar unidades (mentores y Coach)
# ---------------------------------------------------------------------------
@bp.route("/<int:pieza_id>/editar", methods=("GET", "POST"))
@rol_requerido("mentor", "coach")
def editar(pieza_id):
    pieza = models.obtener_pieza(pieza_id)
    if pieza is None or not pieza["activa"]:
        return render_template("error.html", titulo="Pieza no encontrada",
                               mensaje="La pieza no existe o ya no está activa."), 404
    accion = url_for("inventario.editar", pieza_id=pieza_id)

    if request.method == "GET":
        return _mostrar_formulario("editar", dict(pieza), [], accion, pieza=pieza)

    datos, errores = _leer_formulario(request.form, True, _cargar_listas(), con_stock=False)
    if not errores and datos["sku"] and models.sku_en_uso(datos["sku"], excluir_id=pieza_id):
        errores.append(f"El SKU {datos['sku']} ya lo usa otra pieza.")

    imagen_nueva = None
    if not errores:
        try:
            imagen_nueva = _guardar_imagen(request.files.get("imagen_archivo"))
        except ValueError as exc:
            errores.append(str(exc))

    if errores:
        datos["imagen"] = pieza["imagen"]
        return _mostrar_formulario("editar", datos, errores, accion, pieza=pieza)

    # La imagen anterior se toma de la base de datos, nunca del formulario (el navegador
    # podría enviar cualquier texto).
    datos["imagen"] = imagen_nueva or pieza["imagen"]
    models.actualizar_pieza(pieza_id, datos, session["usuario_id"])
    if imagen_nueva:
        _borrar_imagen(pieza["imagen"])
    flash("Ficha actualizada.", "ok")
    return redirect(url_for("inventario.detalle", pieza_id=pieza_id))


@bp.route("/<int:pieza_id>/agregar-unidades", methods=("POST",))
@rol_requerido("mentor", "coach")
def agregar_unidades(pieza_id):
    cantidad = _entero_o_none(request.form.get("cantidad"))
    equipo_id = _entero_o_none(request.form.get("equipo_id"))   # None = stock comunitario
    if cantidad is None or cantidad <= 0:
        flash("La cantidad debe ser un número entero mayor que cero.", "error")
    elif equipo_id is not None and equipo_id not in {e["id"] for e in models.obtener_equipos()}:
        flash("El equipo seleccionado no existe.", "error")
    else:
        try:
            reglas.agregar_unidades(pieza_id, cantidad, equipo_id, session["usuario_id"])
            flash(f"Se agregaron {cantidad} unidades.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
    return redirect(url_for("inventario.detalle", pieza_id=pieza_id))


# ---------------------------------------------------------------------------
# Retirar/eliminar unidades (solo mentores y Coach)
# ---------------------------------------------------------------------------
@bp.route("/<int:pieza_id>/retirar", methods=("POST",))
@rol_requerido("mentor", "coach")
def retirar(pieza_id):
    cantidad = _entero_o_none(request.form.get("cantidad"))
    equipo_id = _entero_o_none(request.form.get("equipo_id"))
    retirar_todo = request.form.get("retirar_todo") == "1"
    motivo = (request.form.get("motivo") or "").strip()

    try:
        if retirar_todo:
            reglas.eliminar_pieza_completa(pieza_id, session["usuario_id"], motivo)
            flash("La pieza fue retirada por completo del inventario.", "ok")
        else:
            if cantidad is None or cantidad <= 0:
                raise ValueError("La cantidad debe ser un número entero mayor que cero.")
            reglas.retirar_unidades(pieza_id, cantidad, equipo_id, session["usuario_id"], motivo)
            flash(f"Se retiraron {cantidad} unidad(es) del inventario.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")

    return redirect(url_for("inventario.detalle", pieza_id=pieza_id))


# ---------------------------------------------------------------------------
# Devoluciones al stock comunitario
# ---------------------------------------------------------------------------
@bp.route("/<int:pieza_id>/devolver", methods=("POST",))
@login_requerido
def devolver(pieza_id):
    cantidad = _entero_o_none(request.form.get("cantidad"))
    if cantidad is None or cantidad <= 0:
        flash("La cantidad debe ser un número entero mayor que cero.", "error")
        return redirect(url_for("inventario.detalle", pieza_id=pieza_id))

    if _es_personal():
        equipo_id = _entero_o_none(request.form.get("equipo_id"))
        if equipo_id is None or equipo_id not in {e["id"] for e in models.obtener_equipos()}:
            flash("Selecciona un equipo válido.", "error")
            return redirect(url_for("inventario.detalle", pieza_id=pieza_id))
    else:
        # Un alumno nunca puede devolver piezas de otro equipo, aunque manipule el formulario.
        equipo_id = session["equipo_id"]

    try:
        reglas.devolver_al_stock(pieza_id, equipo_id, cantidad, session["usuario_id"])
        flash(f"Se devolvieron {cantidad} unidad(es) al stock comunitario.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("inventario.detalle", pieza_id=pieza_id))


# ---------------------------------------------------------------------------
# Solicitudes de compra
# ---------------------------------------------------------------------------
def _leer_solicitud_compra(form):
    errores = []
    datos = {
        "pieza_id": _entero_o_none(form.get("pieza_id")),
        "nombre": form.get("nombre", "").strip(),
        "marca_id": _entero_o_none(form.get("marca_id")),
        "sku": form.get("sku", "").strip(),
        "categoria_id": _entero_o_none(form.get("categoria_id")),
        "cantidad": _entero_o_none(form.get("cantidad")),
        "costo_unitario_estimado": form.get("costo_unitario_estimado", "").strip(),
        "link": form.get("link", "").strip(),
        "motivo": form.get("motivo", "").strip(),
    }
    if not datos["nombre"]:
        errores.append("El nombre de la pieza es obligatorio.")
    elif len(datos["nombre"]) > 120:
        errores.append("El nombre es demasiado largo (máximo 120 caracteres).")

    listas = _cargar_listas()
    marcas_validas = {m["id"] for m in listas["marcas"]}
    categorias_validas = {c["id"] for c in listas["categorias"]}
    if datos["marca_id"] is not None and datos["marca_id"] not in marcas_validas:
        errores.append("La marca seleccionada no existe.")
    if datos["categoria_id"] is not None and datos["categoria_id"] not in categorias_validas:
        errores.append("La categoría seleccionada no existe.")

    if datos["cantidad"] is None or datos["cantidad"] <= 0:
        errores.append("La cantidad debe ser un número entero mayor que cero.")

    try:
        costo = float(datos["costo_unitario_estimado"] or 0)
        if costo < 0:
            raise ValueError
        datos["costo_unitario_estimado"] = costo
    except ValueError:
        errores.append("El costo estimado debe ser un número mayor o igual a cero.")

    if datos["link"] and not _es_url_http(datos["link"]):
        errores.append("El enlace debe empezar con http:// o https://")
    if len(datos["motivo"]) > 500:
        errores.append("El motivo es demasiado largo (máximo 500 caracteres).")
    return datos, errores, listas


@bp.route("/solicitudes-compra/nueva", methods=("GET", "POST"))
@rol_requerido("alumno")
def nueva_solicitud_compra():
    pieza_id = _entero_o_none(request.args.get("pieza_id")) if request.method == "GET" else _entero_o_none(request.form.get("pieza_id"))
    pieza = models.obtener_pieza(pieza_id) if pieza_id is not None else None
    if pieza_id is not None and (pieza is None or not pieza["activa"]):
        return render_template("error.html", titulo="Pieza no encontrada",
                               mensaje="La pieza seleccionada ya no está activa."), 404

    if request.method == "GET":
        datos = {}
        if pieza is not None:
            datos = {
                "pieza_id": pieza["id"], "nombre": pieza["nombre"], "marca_id": pieza["marca_id"],
                "sku": pieza["sku"], "categoria_id": pieza["categoria_id"],
                "cantidad": 1, "costo_unitario_estimado": pieza["costo_unitario"],
                "link": pieza["link"] or "", "motivo": "",
            }
        return render_template(
            "solicitud_compra_form.html", datos=datos, errores=[], pieza=pieza,
            **_cargar_listas(),
        )

    datos, errores, listas = _leer_solicitud_compra(request.form)
    if datos["pieza_id"] is not None:
        pieza = models.obtener_pieza(datos["pieza_id"])
        if pieza is None or not pieza["activa"]:
            errores.append("La pieza seleccionada ya no está activa.")
        else:
            # Para una pieza existente, tomamos los datos del inventario y no del navegador.
            datos.update({
                "nombre": pieza["nombre"], "marca_id": pieza["marca_id"], "sku": pieza["sku"],
                "categoria_id": pieza["categoria_id"], "link": pieza["link"] or "",
            })

    if not errores:
        try:
            reglas.crear_solicitud_compra(datos, session["equipo_id"], session["usuario_id"])
        except ValueError as exc:
            errores.append(str(exc))
        else:
            flash("Solicitud de compra enviada.", "ok")
            return redirect(url_for("inventario.solicitudes_compra"))

    return render_template(
        "solicitud_compra_form.html", datos=datos, errores=errores, pieza=pieza,
        **_cargar_listas(),
    ), 400


@bp.route("/solicitudes-compra")
@login_requerido
def solicitudes_compra():
    equipo_id = None if _es_personal() else session["equipo_id"]
    return render_template(
        "solicitudes_compra.html",
        solicitudes=models.obtener_solicitudes_compra(equipo_id),
    )


@bp.route("/solicitudes-compra/<int:solicitud_id>/resolver", methods=("POST",))
@rol_requerido("mentor", "coach")
def resolver_solicitud_compra(solicitud_id):
    accion = request.form.get("accion")
    motivo = request.form.get("motivo", "").strip()
    try:
        reglas.resolver_solicitud_compra(
            solicitud_id, accion, session["usuario_id"], motivo=motivo
        )
        flash("Solicitud de compra actualizada.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("inventario.solicitudes_compra"))


@bp.route("/solicitudes-compra/<int:solicitud_id>/comprada", methods=("POST",))
@rol_requerido("mentor", "coach")
def marcar_comprada(solicitud_id):
    try:
        reglas.marcar_compra_realizada(solicitud_id, session["usuario_id"])
        flash("La compra quedó marcada como realizada.", "ok")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("inventario.solicitudes_compra"))


@bp.route("/solicitudes-compra/<int:solicitud_id>/recibida", methods=("POST",))
@rol_requerido("mentor", "coach")
def marcar_recibida(solicitud_id):
    try:
        reglas.marcar_compra_recibida(solicitud_id, session["usuario_id"])
        solicitud_actualizada = models.obtener_solicitud_compra(solicitud_id)
        flash(
            f"Compra recibida. Se agregaron {solicitud_actualizada['cantidad']} unidad(es) al inventario y se asignaron al equipo solicitante.",
            "ok",
        )
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("inventario.solicitudes_compra"))


@bp.route("/<int:pieza_id>/solicitar-compra", methods=("POST",))
@rol_requerido("alumno")
def solicitar_compra_desde_pieza(pieza_id):
    pieza = models.obtener_pieza(pieza_id)
    if pieza is None or not pieza["activa"]:
        flash("La pieza no existe o ya no está activa.", "error")
        return redirect(url_for("inventario.lista"))
    return redirect(url_for("inventario.nueva_solicitud_compra", pieza_id=pieza_id))


# ---------------------------------------------------------------------------
# Solicitudes de piezas
# ---------------------------------------------------------------------------
@bp.route("/<int:pieza_id>/solicitar", methods=("POST",))
@rol_requerido("alumno")
def solicitar(pieza_id):
    cantidad = _entero_o_none(request.form.get("cantidad"))
    if cantidad is None or cantidad <= 0:
        flash("La cantidad debe ser un número entero mayor que cero.", "error")
    else:
        try:
            reglas.crear_solicitud(pieza_id, session["equipo_id"], cantidad, session["usuario_id"])
            flash("Solicitud enviada. Un mentor o el Coach la revisará.", "ok")
        except ValueError as exc:
            flash(str(exc), "error")
    return redirect(url_for("inventario.detalle", pieza_id=pieza_id))


@bp.route("/solicitudes")
@login_requerido
def solicitudes():
    # Regla 2: el alumno solo ve las solicitudes de su equipo.
    equipo_id = None if _es_personal() else session["equipo_id"]
    return render_template("solicitudes.html", solicitudes=models.obtener_solicitudes(equipo_id))


@bp.route("/solicitudes/<int:solicitud_id>/resolver", methods=("POST",))
@rol_requerido("mentor", "coach")
def resolver_solicitud(solicitud_id):
    accion = request.form.get("accion")
    try:
        if accion == "aprobar":
            reglas.aprobar_solicitud(solicitud_id, session["usuario_id"])
            flash("Solicitud aprobada: las piezas ya son del equipo.", "ok")
        elif accion == "rechazar":
            motivo = request.form.get("motivo", "").strip() or "Sin motivo indicado."
            reglas.rechazar_solicitud(solicitud_id, session["usuario_id"], motivo)
            flash("Solicitud rechazada.", "ok")
        else:
            flash("Acción no válida.", "error")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("inventario.solicitudes"))
