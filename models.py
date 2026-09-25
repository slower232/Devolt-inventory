"""
models.py
Consultas SQL sencillas. Las rutas llaman a estas funciones en lugar de escribir SQL,
así cada consulta vive en un solo lugar.

Regla: aquí solo se LEE o se hacen cambios de un solo paso (por ejemplo, editar un nombre).
Las operaciones que mueven piezas o escriben en el historial están en reglas.py.
"""
from werkzeug.security import generate_password_hash

from database import get_db


# ===========================================================================
# USUARIOS
# ===========================================================================
def obtener_usuario_por_username(username):
    """Busca por nombre de usuario. El LEFT JOIN trae el nombre del equipo
    (mentores y Coach no tienen equipo, por eso es LEFT y no JOIN normal)."""
    return get_db().execute(
        "SELECT u.*, e.nombre AS equipo_nombre FROM usuarios u "
        "LEFT JOIN equipos e ON e.id = u.equipo_id WHERE u.username = ? COLLATE NOCASE", (username,)
    ).fetchone()


def obtener_usuario(usuario_id):
    return get_db().execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()


def obtener_usuario_con_equipo(usuario_id):
    return get_db().execute(
        "SELECT u.*, e.nombre AS equipo_nombre FROM usuarios u "
        "LEFT JOIN equipos e ON e.id = u.equipo_id WHERE u.id = ?", (usuario_id,)
    ).fetchone()


def obtener_usuarios():
    return get_db().execute(
        "SELECT u.*, e.nombre AS equipo_nombre FROM usuarios u "
        "LEFT JOIN equipos e ON e.id = u.equipo_id "
        "ORDER BY u.activo DESC, u.nombre COLLATE NOCASE"
    ).fetchall()


def obtener_equipos():
    return get_db().execute("SELECT id, nombre FROM equipos ORDER BY id").fetchall()


def username_en_uso(username, excluir_id=None):
    """True si otro usuario ya tiene ese nombre de usuario (al editar se excluye al propio)."""
    fila = get_db().execute(
        "SELECT id FROM usuarios WHERE username = ? COLLATE NOCASE AND id <> ?",
        (username, excluir_id or 0),
    ).fetchone()
    return fila is not None


def crear_usuario(nombre, username, password, rol, equipo_id=None):
    """Crea la cuenta con contraseña temporal: debe cambiarla al entrar."""
    db = get_db()
    cursor = db.execute(
        "INSERT INTO usuarios (nombre, username, password_hash, rol, equipo_id, debe_cambiar_password) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (nombre, username, generate_password_hash(password), rol, equipo_id),
    )
    db.commit()
    return cursor.lastrowid


def actualizar_usuario(usuario_id, nombre, username, rol, equipo_id, activo):
    db = get_db()
    db.execute(
        "UPDATE usuarios SET nombre = ?, username = ?, rol = ?, equipo_id = ?, activo = ? WHERE id = ?",
        (nombre, username, rol, equipo_id, int(activo), usuario_id),
    )
    db.commit()


def cambiar_estado_usuario(usuario_id, activo):
    db = get_db()
    db.execute("UPDATE usuarios SET activo = ? WHERE id = ?", (int(activo), usuario_id))
    db.commit()


def cambiar_password(usuario_id, nueva_password):
    """Guarda la contraseña como hash y quita la obligación de cambiarla."""
    db = get_db()
    db.execute("UPDATE usuarios SET password_hash = ?, debe_cambiar_password = 0 WHERE id = ?",
               (generate_password_hash(nueva_password), usuario_id))
    db.commit()


def restablecer_password(usuario_id, password_temporal):
    """El Coach genera una contraseña temporal; el usuario deberá cambiarla al entrar."""
    db = get_db()
    db.execute("UPDATE usuarios SET password_hash = ?, debe_cambiar_password = 1 WHERE id = ?",
               (generate_password_hash(password_temporal), usuario_id))
    db.commit()


# ===========================================================================
# CATÁLOGOS: marcas, categorías y ubicaciones
# Las tres tablas tienen la misma forma (id, nombre), así que comparten funciones.
# El diccionario dice, para cada tabla, qué columna de "piezas" apunta a ella.
# Los nombres de tabla NUNCA se toman directamente del navegador: se validan aquí.
# ===========================================================================
CATALOGOS = {
    "marcas": "marca_id",
    "categorias": "categoria_id",
    "ubicaciones": "ubicacion_id",
}


def _columna_en_piezas(tabla):
    if tabla not in CATALOGOS:
        raise ValueError("Catálogo desconocido.")
    return CATALOGOS[tabla]


def catalogo_listar(tabla):
    """Devuelve (id, nombre, en_uso): en_uso = cuántas piezas activas lo usan."""
    columna = _columna_en_piezas(tabla)
    return get_db().execute(
        f"SELECT t.id, t.nombre, "
        f"(SELECT COUNT(*) FROM piezas p WHERE p.{columna} = t.id AND p.activa = 1) AS en_uso "
        f"FROM {tabla} t ORDER BY t.nombre COLLATE NOCASE"
    ).fetchall()


def obtener_marcas():
    return get_db().execute("SELECT id, nombre FROM marcas ORDER BY nombre COLLATE NOCASE").fetchall()


def obtener_categorias():
    return get_db().execute("SELECT id, nombre FROM categorias ORDER BY nombre COLLATE NOCASE").fetchall()


def obtener_ubicaciones():
    return get_db().execute("SELECT id, nombre FROM ubicaciones ORDER BY id").fetchall()


def catalogo_nombre_repetido(tabla, nombre, excluir_id=None):
    """True si ya existe ese nombre, sin distinguir mayúsculas ("GoBilda" = "gobilda")."""
    _columna_en_piezas(tabla)
    fila = get_db().execute(
        f"SELECT id FROM {tabla} WHERE nombre = ? COLLATE NOCASE AND id <> ?",
        (nombre, excluir_id or 0),
    ).fetchone()
    return fila is not None


def catalogo_agregar(tabla, nombre):
    _columna_en_piezas(tabla)
    db = get_db()
    db.execute(f"INSERT INTO {tabla} (nombre) VALUES (?)", (nombre,))
    db.commit()


def catalogo_renombrar(tabla, item_id, nombre):
    _columna_en_piezas(tabla)
    db = get_db()
    db.execute(f"UPDATE {tabla} SET nombre = ? WHERE id = ?", (nombre, item_id))
    db.commit()


def catalogo_eliminar(tabla, item_id):
    """Solo se puede eliminar si ninguna pieza (ni siquiera una oculta) lo usa."""
    columna = _columna_en_piezas(tabla)
    db = get_db()
    usadas = db.execute(f"SELECT COUNT(*) AS n FROM piezas WHERE {columna} = ?", (item_id,)).fetchone()["n"]
    if usadas:
        raise ValueError(f"No se puede eliminar: {usadas} pieza(s) lo usan.")
    db.execute(f"DELETE FROM {tabla} WHERE id = ?", (item_id,))
    db.commit()


# ===========================================================================
# PIEZAS
# "disponible" (stock comunitario) NO se guarda: se calcula aquí cada vez.
#     disponible = cantidad_total - suma de lo asignado a los equipos
# ===========================================================================
_SQL_PIEZA = (
    "SELECT p.*, m.nombre AS marca_nombre, c.nombre AS categoria_nombre, u.nombre AS ubicacion_nombre, "
    "COALESCE((SELECT SUM(a.cantidad) FROM asignaciones a WHERE a.pieza_id = p.id), 0) AS asignada, "
    "p.cantidad_total - COALESCE((SELECT SUM(a.cantidad) FROM asignaciones a WHERE a.pieza_id = p.id), 0) AS disponible "
    "FROM piezas p "
    "LEFT JOIN marcas m ON m.id = p.marca_id "
    "LEFT JOIN categorias c ON c.id = p.categoria_id "
    "LEFT JOIN ubicaciones u ON u.id = p.ubicacion_id "
)


def obtener_inventario(filtros=None):
    """Piezas activas con búsqueda y filtros opcionales.

    filtros puede contener: q, marca_id, categoria_id, ubicacion_id y disponibilidad
    ("disponible", "agotada" o vacío).
    """
    filtros = filtros or {}
    condiciones = ["p.activa = 1"]
    parametros = []

    q = (filtros.get("q") or "").strip()
    if q:
        termino = f"%{q}%"
        condiciones.append("(p.nombre LIKE ? COLLATE NOCASE OR COALESCE(p.sku, '') LIKE ? COLLATE NOCASE OR COALESCE(m.nombre, '') LIKE ? COLLATE NOCASE)")
        parametros.extend([termino, termino, termino])

    for clave, columna in (("marca_id", "p.marca_id"), ("categoria_id", "p.categoria_id"), ("ubicacion_id", "p.ubicacion_id")):
        valor = filtros.get(clave)
        if valor not in (None, ""):
            try:
                entero = int(valor)
            except (TypeError, ValueError):
                entero = None
            if entero is not None:
                condiciones.append(f"{columna} = ?")
                parametros.append(entero)

    disponibilidad = (filtros.get("disponibilidad") or "").strip().lower()
    expresion_disponible = "p.cantidad_total - COALESCE((SELECT SUM(a2.cantidad) FROM asignaciones a2 WHERE a2.pieza_id = p.id), 0)"
    if disponibilidad == "disponible":
        condiciones.append(f"{expresion_disponible} > 0")
    elif disponibilidad == "agotada":
        condiciones.append(f"{expresion_disponible} <= 0")

    sql = _SQL_PIEZA + "WHERE " + " AND ".join(condiciones) + " ORDER BY p.nombre COLLATE NOCASE"
    return get_db().execute(sql, parametros).fetchall()


def obtener_pieza(pieza_id):
    return get_db().execute(_SQL_PIEZA + "WHERE p.id = ?", (pieza_id,)).fetchone()


def obtener_asignaciones_pieza(pieza_id):
    """Cuántas unidades de la pieza tiene cada equipo (0 si ninguna)."""
    return get_db().execute(
        "SELECT e.id, e.nombre, COALESCE(a.cantidad, 0) AS cantidad "
        "FROM equipos e LEFT JOIN asignaciones a ON a.equipo_id = e.id AND a.pieza_id = ? "
        "ORDER BY e.id", (pieza_id,)
    ).fetchall()


def sku_en_uso(sku, excluir_id=None):
    """True si otra pieza (activa o no) ya usa ese SKU."""
    fila = get_db().execute(
        "SELECT id FROM piezas WHERE sku = ? COLLATE NOCASE AND id <> ?", (sku, excluir_id or 0)
    ).fetchone()
    return fila is not None


def actualizar_pieza(pieza_id, datos, usuario_id=None):
    """Edita los datos de la ficha. Las cantidades se cambian por separado."""
    db = get_db()
    db.execute(
        "UPDATE piezas SET nombre = ?, marca_id = ?, sku = ?, categoria_id = ?, costo_unitario = ?, "
        "ubicacion_id = ?, imagen = ?, link = ? WHERE id = ?",
        (datos["nombre"], datos["marca_id"], datos["sku"] or None, datos["categoria_id"],
         datos["costo_unitario"], datos["ubicacion_id"], datos["imagen"], datos["link"] or None, pieza_id),
    )
    if usuario_id is not None:
        db.execute(
            "INSERT INTO historial (tipo, usuario_id, pieza_id, motivo) VALUES ('pieza_editada', ?, ?, ?)",
            (usuario_id, pieza_id, "Se actualizó la ficha de la pieza."),
        )
    db.commit()


# ===========================================================================
# SOLICITUDES (solo lectura; crear, aprobar y rechazar están en reglas.py)
# ===========================================================================
def obtener_solicitudes(equipo_id=None):
    """Todas las solicitudes, o solo las de un equipo. Las pendientes salen primero."""
    sql = (
        "SELECT s.*, p.nombre AS pieza_nombre, p.sku, e.nombre AS equipo_nombre, "
        "u.nombre AS solicitante_nombre, r.nombre AS resolutor_nombre "
        "FROM solicitudes s JOIN piezas p ON p.id = s.pieza_id "
        "JOIN equipos e ON e.id = s.equipo_id JOIN usuarios u ON u.id = s.solicitante_id "
        "LEFT JOIN usuarios r ON r.id = s.resuelto_por_id "
    )
    parametros = ()
    if equipo_id is not None:
        sql += "WHERE s.equipo_id = ? "
        parametros = (equipo_id,)
    sql += "ORDER BY CASE s.estado WHEN 'pendiente' THEN 0 WHEN 'aprobada' THEN 1 ELSE 2 END, s.fecha_solicitud DESC"
    return get_db().execute(sql, parametros).fetchall()


def obtener_resumen_inventario(equipo_id=None):
    """Números para las tarjetas del dashboard provisional."""
    db = get_db()
    piezas = db.execute("SELECT COUNT(*) AS n FROM piezas WHERE activa = 1").fetchone()["n"]
    disponibles = db.execute(
        "SELECT COALESCE(SUM(p.cantidad_total - COALESCE("
        "(SELECT SUM(a.cantidad) FROM asignaciones a WHERE a.pieza_id = p.id), 0)), 0) AS n "
        "FROM piezas p WHERE p.activa = 1"
    ).fetchone()["n"]
    sql = "SELECT COUNT(*) AS n FROM solicitudes WHERE estado = 'pendiente'"
    parametros = ()
    if equipo_id is not None:
        sql += " AND equipo_id = ?"
        parametros = (equipo_id,)
    pendientes = db.execute(sql, parametros).fetchone()["n"]
    compras_sql = "SELECT COUNT(*) AS n FROM solicitudes_compra WHERE estado = 'pendiente'"
    compras_parametros = ()
    if equipo_id is not None:
        compras_sql += " AND equipo_id = ?"
        compras_parametros = (equipo_id,)
    compras_pendientes = db.execute(compras_sql, compras_parametros).fetchone()["n"]
    return {
        "piezas": piezas,
        "unidades_disponibles": disponibles,
        "solicitudes_pendientes": pendientes,
        "compras_pendientes": compras_pendientes,
    }


# ===========================================================================
# DEVOLUCIONES
# ===========================================================================
def obtener_asignacion_equipo(pieza_id, equipo_id):
    return get_db().execute(
        "SELECT cantidad FROM asignaciones WHERE pieza_id = ? AND equipo_id = ?",
        (pieza_id, equipo_id),
    ).fetchone()


# ===========================================================================
# SOLICITUDES DE COMPRA
# ===========================================================================
def obtener_solicitudes_compra(equipo_id=None):
    sql = (
        "SELECT sc.*, e.nombre AS equipo_nombre, u.nombre AS solicitante_nombre, "
        "r.nombre AS resolutor_nombre, m.nombre AS marca_nombre, c.nombre AS categoria_nombre "
        "FROM solicitudes_compra sc "
        "JOIN equipos e ON e.id = sc.equipo_id "
        "JOIN usuarios u ON u.id = sc.solicitante_id "
        "LEFT JOIN usuarios r ON r.id = sc.resuelto_por_id "
        "LEFT JOIN marcas m ON m.id = sc.marca_id "
        "LEFT JOIN categorias c ON c.id = sc.categoria_id "
    )
    parametros = ()
    if equipo_id is not None:
        sql += "WHERE sc.equipo_id = ? "
        parametros = (equipo_id,)
    sql += (
        "ORDER BY CASE sc.estado "
        "WHEN 'pendiente' THEN 0 WHEN 'aprobada' THEN 1 "
        "WHEN 'comprada' THEN 2 WHEN 'recibida' THEN 3 ELSE 4 END, "
        "sc.fecha_solicitud DESC"
    )
    return get_db().execute(sql, parametros).fetchall()


def obtener_solicitud_compra(solicitud_id):
    return get_db().execute(
        "SELECT * FROM solicitudes_compra WHERE id = ?", (solicitud_id,)
    ).fetchone()


def contar_solicitudes_compra_pendientes(equipo_id=None):
    sql = "SELECT COUNT(*) AS n FROM solicitudes_compra WHERE estado = 'pendiente'"
    parametros = ()
    if equipo_id is not None:
        sql += " AND equipo_id = ?"
        parametros = (equipo_id,)
    return get_db().execute(sql, parametros).fetchone()["n"]

# ===========================================================================
# HISTORIAL
# ===========================================================================
def obtener_historial(filtros=None, limite=250):
    """Devuelve la bitácora con nombres legibles y filtros opcionales."""
    filtros = filtros or {}
    condiciones = []
    parametros = []

    tipo = (filtros.get("tipo") or "").strip()
    if tipo:
        condiciones.append("h.tipo = ?")
        parametros.append(tipo)

    equipo_id = filtros.get("equipo_id")
    if equipo_id not in (None, ""):
        try:
            equipo_id = int(equipo_id)
        except (TypeError, ValueError):
            equipo_id = None
        if equipo_id is not None:
            condiciones.append("h.equipo_id = ?")
            parametros.append(equipo_id)

    usuario_id = filtros.get("usuario_id")
    if usuario_id not in (None, ""):
        try:
            usuario_id = int(usuario_id)
        except (TypeError, ValueError):
            usuario_id = None
        if usuario_id is not None:
            condiciones.append("h.usuario_id = ?")
            parametros.append(usuario_id)

    pieza_id = filtros.get("pieza_id")
    if pieza_id not in (None, ""):
        try:
            pieza_id = int(pieza_id)
        except (TypeError, ValueError):
            pieza_id = None
        if pieza_id is not None:
            condiciones.append("h.pieza_id = ?")
            parametros.append(pieza_id)

    q = (filtros.get("q") or "").strip()
    if q:
        termino = f"%{q}%"
        condiciones.append("(COALESCE(p.nombre, '') LIKE ? COLLATE NOCASE OR COALESCE(p.sku, '') LIKE ? COLLATE NOCASE OR u.nombre LIKE ? COLLATE NOCASE OR COALESCE(e.nombre, '') LIKE ? COLLATE NOCASE)")
        parametros.extend([termino, termino, termino, termino])

    sql = (
        "SELECT h.*, u.nombre AS usuario_nombre, e.nombre AS equipo_nombre, "
        "p.nombre AS pieza_nombre, p.sku "
        "FROM historial h "
        "JOIN usuarios u ON u.id = h.usuario_id "
        "LEFT JOIN equipos e ON e.id = h.equipo_id "
        "LEFT JOIN piezas p ON p.id = h.pieza_id "
    )
    if condiciones:
        sql += "WHERE " + " AND ".join(condiciones) + " "
    sql += "ORDER BY h.id DESC LIMIT ?"
    parametros.append(max(1, min(int(limite), 1000)))
    return get_db().execute(sql, parametros).fetchall()


def obtener_tipos_historial():
    return get_db().execute(
        "SELECT DISTINCT tipo FROM historial ORDER BY tipo COLLATE NOCASE"
    ).fetchall()

