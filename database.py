"""
database.py
Todo lo relacionado con la base de datos SQLite:
  - abrir y cerrar la conexión
  - crear las tablas la primera vez
  - insertar los datos iniciales (equipos, marcas, ubicaciones, categorías y el Coach)
"""
import os
import sqlite3
from contextlib import contextmanager

from flask import g
from werkzeug.security import generate_password_hash

# Carpeta donde vive este archivo, para que la ruta funcione sin importar
# desde dónde se ejecute el programa.
CARPETA_BASE = os.path.dirname(os.path.abspath(__file__))
RUTA_DB = os.path.join(CARPETA_BASE, "database", "inventario.db")


# ---------------------------------------------------------------------------
# ESQUEMA (las tablas)
# ---------------------------------------------------------------------------
# Regla clave: NO existe una columna "cantidad disponible".
#   disponible = piezas.cantidad_total - SUM(asignaciones.cantidad)
# Así el stock comunitario nunca queda desactualizado.
ESQUEMA = """
CREATE TABLE IF NOT EXISTS equipos (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS usuarios (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre                TEXT NOT NULL,
    username              TEXT NOT NULL UNIQUE,
    password_hash         TEXT NOT NULL,          -- nunca se guarda la contraseña real
    rol                   TEXT NOT NULL CHECK (rol IN ('alumno', 'mentor', 'coach')),
    equipo_id             INTEGER REFERENCES equipos(id),
    activo                INTEGER NOT NULL DEFAULT 1,   -- 0 = cuenta eliminada (se conserva el historial)
    debe_cambiar_password INTEGER NOT NULL DEFAULT 0,
    fecha_creacion        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    -- Regla 1: todo alumno pertenece a un equipo
    CHECK (rol <> 'alumno' OR equipo_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS marcas (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS categorias (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS ubicaciones (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS piezas (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre         TEXT NOT NULL,
    marca_id       INTEGER REFERENCES marcas(id),
    sku            TEXT,
    categoria_id   INTEGER REFERENCES categorias(id),
    cantidad_total INTEGER NOT NULL DEFAULT 0 CHECK (cantidad_total >= 0),
    costo_unitario REAL    NOT NULL DEFAULT 0 CHECK (costo_unitario >= 0),
    imagen         TEXT,                      -- nombre del archivo en static/images/piezas
    link           TEXT,
    ubicacion_id   INTEGER REFERENCES ubicaciones(id),
    fecha_registro TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    activa         INTEGER NOT NULL DEFAULT 1  -- 0 = ficha oculta (no se borra por el historial)
);
-- El SKU no se puede repetir, pero se permite dejarlo vacío.
CREATE UNIQUE INDEX IF NOT EXISTS idx_piezas_sku
    ON piezas(sku) WHERE sku IS NOT NULL AND sku <> '';

-- Cuántas unidades de cada pieza tiene cada equipo.
-- Lo que no está asignado a ningún equipo es stock comunitario.
CREATE TABLE IF NOT EXISTS asignaciones (
    pieza_id  INTEGER NOT NULL REFERENCES piezas(id),
    equipo_id INTEGER NOT NULL REFERENCES equipos(id),
    cantidad  INTEGER NOT NULL DEFAULT 0 CHECK (cantidad >= 0),
    PRIMARY KEY (pieza_id, equipo_id)
);

-- "cantidad" = unidades ADICIONALES que al equipo todavía le hacen falta
-- (aunque ya tenga otras). Ejemplo: tiene 6 motores y le faltan 2 -> cantidad = 2.
CREATE TABLE IF NOT EXISTS necesidades (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    equipo_id INTEGER NOT NULL REFERENCES equipos(id),
    pieza_id  INTEGER NOT NULL REFERENCES piezas(id),
    cantidad  INTEGER NOT NULL CHECK (cantidad >= 0),
    tipo      TEXT NOT NULL DEFAULT 'construccion' CHECK (tipo IN ('construccion', 'repuesto')),
    UNIQUE (equipo_id, pieza_id, tipo)
);

-- Material a granel (tornillos, tuercas...). No se controla cuántos hay:
-- solo se anota lo que un equipo requiere y su costo aproximado.
CREATE TABLE IF NOT EXISTS necesidades_granel (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    equipo_id                INTEGER NOT NULL REFERENCES equipos(id),
    descripcion              TEXT NOT NULL,          -- ej. "Tornillo M3 x 10"
    cantidad                 INTEGER NOT NULL CHECK (cantidad > 0),
    costo_unitario_estimado  REAL NOT NULL DEFAULT 0 CHECK (costo_unitario_estimado >= 0),
    estado                   TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'comprado')),
    creado_por_id            INTEGER NOT NULL REFERENCES usuarios(id),
    fecha                    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    marcado_por_id           INTEGER REFERENCES usuarios(id),
    fecha_compra             TEXT
);

CREATE TABLE IF NOT EXISTS solicitudes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    pieza_id         INTEGER NOT NULL REFERENCES piezas(id),
    equipo_id        INTEGER NOT NULL REFERENCES equipos(id),
    cantidad         INTEGER NOT NULL CHECK (cantidad > 0),
    solicitante_id   INTEGER NOT NULL REFERENCES usuarios(id),
    estado           TEXT NOT NULL DEFAULT 'pendiente'
                     CHECK (estado IN ('pendiente', 'aprobada', 'rechazada')),
    fecha_solicitud  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    resuelto_por_id  INTEGER REFERENCES usuarios(id),
    fecha_resolucion TEXT,
    motivo_rechazo   TEXT
);

-- Compras de piezas: 'comprado' (en camino) -> 'recibido' (ya está en el taller)
CREATE TABLE IF NOT EXISTS solicitudes_compra (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    equipo_id             INTEGER NOT NULL REFERENCES equipos(id),
    solicitante_id        INTEGER NOT NULL REFERENCES usuarios(id),
    pieza_id              INTEGER REFERENCES piezas(id),
    nombre                TEXT NOT NULL,
    marca_id              INTEGER REFERENCES marcas(id),
    sku                   TEXT,
    categoria_id          INTEGER REFERENCES categorias(id),
    cantidad              INTEGER NOT NULL CHECK (cantidad > 0),
    costo_unitario_estimado REAL NOT NULL DEFAULT 0 CHECK (costo_unitario_estimado >= 0),
    link                  TEXT,
    motivo                TEXT,
    estado                TEXT NOT NULL DEFAULT 'pendiente'
                          CHECK (estado IN ('pendiente', 'aprobada', 'rechazada', 'comprada', 'recibida')),
    fecha_solicitud       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    resuelto_por_id       INTEGER REFERENCES usuarios(id),
    fecha_resolucion     TEXT,
    fecha_compra          TEXT,
    fecha_recepcion       TEXT,
    motivo_rechazo        TEXT
);

CREATE TABLE IF NOT EXISTS compras (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    pieza_id         INTEGER NOT NULL REFERENCES piezas(id),
    cantidad         INTEGER NOT NULL CHECK (cantidad > 0),
    estado           TEXT NOT NULL DEFAULT 'comprado' CHECK (estado IN ('comprado', 'recibido')),
    marcado_por_id   INTEGER NOT NULL REFERENCES usuarios(id),
    fecha_compra     TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    fecha_recepcion  TEXT
);

-- Bitácora de todo lo importante. Las filas nunca se borran.
CREATE TABLE IF NOT EXISTS historial (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    tipo       TEXT NOT NULL,   -- solicitud, aprobacion, rechazo, devolucion, retiro_danado, ...
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    equipo_id  INTEGER REFERENCES equipos(id),
    pieza_id   INTEGER REFERENCES piezas(id),
    cantidad   INTEGER,
    motivo     TEXT
);
"""

# ---------------------------------------------------------------------------
# DATOS INICIALES
# ---------------------------------------------------------------------------
EQUIPOS = ["Devolt Deimos", "Devolt Phobos", "Devolt Kova"]
MARCAS = ["GoBilda", "REV", "AndyMark"]
CATEGORIAS = ["Motores", "Servos", "Engranes", "Standoffs", "Quadblocks",
              "Poleas", "Ruedas", "Placas", "Electrónica", "Otros"]
UBICACIONES = ["Estantería nivel 1", "Estantería nivel 2", "Estantería nivel 3",
               "Estantería nivel 4", "Estantería nivel 5", "Binera grande", "Binera chica"]


# ---------------------------------------------------------------------------
# CONEXIÓN
# ---------------------------------------------------------------------------
def conectar():
    """Abre una conexión nueva a la base de datos."""
    conexion = sqlite3.connect(RUTA_DB)
    conexion.row_factory = sqlite3.Row            # permite usar fila["nombre"]
    conexion.execute("PRAGMA foreign_keys = ON")  # SQLite no revisa las claves foráneas si no se pide
    return conexion


def get_db():
    """Devuelve la conexión de la petición actual (se abre una sola vez por petición)."""
    if "db" not in g:
        g.db = conectar()
    return g.db


def cerrar_db(error=None):
    """Flask llama a esta función al terminar cada petición."""
    conexion = g.pop("db", None)
    if conexion is not None:
        conexion.close()


@contextmanager
def transaccion():
    """Agrupa varias escrituras en una sola operación "todo o nada".

    Uso:
        with transaccion() as db:
            db.execute(...)
            db.execute(...)
    Si el bloque termina bien, se guardan todos los cambios (commit).
    Si ocurre cualquier error, se deshacen todos (rollback) y el error se vuelve a lanzar.
    BEGIN IMMEDIATE bloquea las escrituras de otros mientras dura, así dos aprobaciones
    al mismo tiempo no pueden asignar la misma pieza dos veces.
    """
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# PRIMER ARRANQUE
# ---------------------------------------------------------------------------
def _llenar_tabla_si_vacia(conexion, tabla, nombres):
    """Inserta los nombres solo si la tabla está vacía (así no se duplican al reiniciar)."""
    total = conexion.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
    if total == 0:
        conexion.executemany(f"INSERT INTO {tabla} (nombre) VALUES (?)",
                             [(nombre,) for nombre in nombres])


PASSWORD_COACH_INICIAL = "Devolt9134"


def _crear_coach_inicial(conexion):
    """Crea el Coach en el primer arranque con una contraseña fija.

    La contraseña sigue siendo temporal porque debe cambiarse al primer inicio.
    Si ya existe una cuenta Coach cuyo cambio de contraseña sigue pendiente, se
    normaliza a la contraseña inicial fija para facilitar migraciones desde
    versiones anteriores que usaban una contraseña aleatoria.
    """
    coach = conexion.execute(
        "SELECT id, debe_cambiar_password FROM usuarios WHERE rol = 'coach' ORDER BY id LIMIT 1"
    ).fetchone()
    if coach is None:
        conexion.execute(
            "INSERT INTO usuarios (nombre, username, password_hash, rol, debe_cambiar_password) "
            "VALUES (?, ?, ?, 'coach', 1)",
            ("Coach", "coach", generate_password_hash(PASSWORD_COACH_INICIAL)),
        )
        print("\n" + "=" * 56)
        print(" PRIMER ARRANQUE - cuenta de Coach creada")
        print("   Usuario:               coach")
        print("   Contraseña inicial:    Devolt9134")
        print(" Se pedirá cambiarla al iniciar sesión.")
        print("=" * 56 + "\n")
        return

    # Migración de v1-v5: si la instalación todavía está esperando el primer
    # cambio, usamos la contraseña fija nueva. Una vez que el Coach la cambió,
    # jamás la sobrescribimos en cada arranque.
    if coach["debe_cambiar_password"]:
        conexion.execute(
            "UPDATE usuarios SET password_hash = ?, debe_cambiar_password = 1 WHERE id = ?",
            (generate_password_hash(PASSWORD_COACH_INICIAL), coach["id"]),
        )


def iniciar_db():
    """Crea la carpeta, las tablas y los datos iniciales. Es seguro llamarla en cada arranque."""
    os.makedirs(os.path.dirname(RUTA_DB), exist_ok=True)
    conexion = conectar()
    try:
        conexion.executescript(ESQUEMA)
        _llenar_tabla_si_vacia(conexion, "equipos", EQUIPOS)
        _llenar_tabla_si_vacia(conexion, "marcas", MARCAS)
        _llenar_tabla_si_vacia(conexion, "categorias", CATEGORIAS)
        _llenar_tabla_si_vacia(conexion, "ubicaciones", UBICACIONES)
        _crear_coach_inicial(conexion)
        conexion.commit()
    finally:
        conexion.close()
