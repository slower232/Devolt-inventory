"""
reglas.py
Las reglas de negocio del taller: todo lo que MUEVE piezas o cambia cantidades.

Cada función:
  1. valida (y lanza ValueError con un mensaje claro si algo no cuadra),
  2. hace todos sus cambios dentro de una transacción ("todo o nada"),
  3. anota lo ocurrido en el historial.

Ninguna cantidad "disponible" se guarda: siempre se calcula
    disponible = cantidad_total - suma de lo asignado a los equipos
"""
import models
from database import transaccion


def _anotar_historial(db, tipo, usuario_id, equipo_id=None, pieza_id=None, cantidad=None, motivo=None):
    """Agrega una línea a la bitácora. Se usa la misma conexión (db) de la transacción."""
    db.execute(
        "INSERT INTO historial (tipo, usuario_id, equipo_id, pieza_id, cantidad, motivo) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (tipo, usuario_id, equipo_id, pieza_id, cantidad, motivo),
    )


def _disponible(db, pieza_id):
    """Unidades en stock comunitario de una pieza activa; None si la pieza no existe o está oculta."""
    fila = db.execute(
        "SELECT cantidad_total - COALESCE((SELECT SUM(cantidad) FROM asignaciones "
        "WHERE pieza_id = piezas.id), 0) AS disponible FROM piezas WHERE id = ? AND activa = 1",
        (pieza_id,),
    ).fetchone()
    return None if fila is None else fila["disponible"]


def _sumar_a_equipo(db, pieza_id, equipo_id, cantidad):
    """Suma unidades a lo que tiene un equipo. Si no tenía fila, la crea (UPSERT)."""
    db.execute(
        "INSERT INTO asignaciones (pieza_id, equipo_id, cantidad) VALUES (?, ?, ?) "
        "ON CONFLICT(pieza_id, equipo_id) DO UPDATE SET cantidad = cantidad + excluded.cantidad",
        (pieza_id, equipo_id, cantidad),
    )


# ===========================================================================
# ALTA DE PIEZAS
# ===========================================================================
def crear_pieza(datos, usuario_id):
    """Registra una pieza nueva. Devuelve su id.

    "datos" ya viene validado por la ruta. Si el mentor/Coach eligió un equipo como
    asignación inicial, todas las unidades nacen asignadas a ese equipo; si no,
    nacen en el stock comunitario. Los alumnos solo crean la ficha (cantidad 0).
    """
    with transaccion() as db:
        if datos["sku"] and models.sku_en_uso(datos["sku"]):
            raise ValueError(f"El SKU {datos['sku']} ya existe en el inventario.")

        cursor = db.execute(
            "INSERT INTO piezas (nombre, marca_id, sku, categoria_id, cantidad_total, costo_unitario, "
            "imagen, link, ubicacion_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (datos["nombre"], datos["marca_id"], datos["sku"] or None, datos["categoria_id"],
             datos["cantidad_total"], datos["costo_unitario"], datos["imagen"],
             datos["link"] or None, datos["ubicacion_id"]),
        )
        pieza_id = cursor.lastrowid

        if datos["equipo_id"] and datos["cantidad_total"] > 0:
            _sumar_a_equipo(db, pieza_id, datos["equipo_id"], datos["cantidad_total"])

        _anotar_historial(db, "pieza_nueva", usuario_id, equipo_id=datos["equipo_id"],
                          pieza_id=pieza_id, cantidad=datos["cantidad_total"])
    return pieza_id


def agregar_unidades(pieza_id, cantidad, equipo_id, usuario_id):
    """Suma unidades a una pieza que ya existe (por ejemplo, llegó una compra).
    equipo_id = None -> van al stock comunitario; si trae un id, se asignan a ese equipo."""
    if cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor que cero.")
    with transaccion() as db:
        pieza = db.execute("SELECT id FROM piezas WHERE id = ? AND activa = 1", (pieza_id,)).fetchone()
        if pieza is None:
            raise ValueError("La pieza no existe o ya no está activa.")
        db.execute("UPDATE piezas SET cantidad_total = cantidad_total + ? WHERE id = ?", (cantidad, pieza_id))
        if equipo_id:
            _sumar_a_equipo(db, pieza_id, equipo_id, cantidad)
        _anotar_historial(db, "unidades_agregadas", usuario_id, equipo_id=equipo_id,
                          pieza_id=pieza_id, cantidad=cantidad)


# ===========================================================================
# SOLICITUDES
# Un alumno no retira piezas: pide. Un mentor o el Coach aprueba (y eso es también la entrega).
# ===========================================================================
def crear_solicitud(pieza_id, equipo_id, cantidad, solicitante_id):
    """El alumno pide piezas del stock comunitario para su equipo. Queda 'pendiente'."""
    if cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor que cero.")
    with transaccion() as db:
        disponible = _disponible(db, pieza_id)
        if disponible is None:
            raise ValueError("La pieza no existe o ya no está activa.")
        # Lo que este equipo ya pidió y sigue esperando cuenta como "apartado" por él.
        ya_pedido = db.execute(
            "SELECT COALESCE(SUM(cantidad), 0) AS n FROM solicitudes "
            "WHERE pieza_id = ? AND equipo_id = ? AND estado = 'pendiente'", (pieza_id, equipo_id)
        ).fetchone()["n"]
        if cantidad + ya_pedido > disponible:
            raise ValueError(f"Solo hay {disponible} disponible(s) y tu equipo ya tiene {ya_pedido} "
                             "en solicitudes pendientes.")
        cursor = db.execute(
            "INSERT INTO solicitudes (pieza_id, equipo_id, cantidad, solicitante_id) VALUES (?, ?, ?, ?)",
            (pieza_id, equipo_id, cantidad, solicitante_id),
        )
        _anotar_historial(db, "solicitud", solicitante_id, equipo_id=equipo_id,
                          pieza_id=pieza_id, cantidad=cantidad)
    return cursor.lastrowid


def _obtener_solicitud_pendiente(db, solicitud_id):
    solicitud = db.execute("SELECT * FROM solicitudes WHERE id = ?", (solicitud_id,)).fetchone()
    if solicitud is None or solicitud["estado"] != "pendiente":
        raise ValueError("La solicitud no existe o ya fue resuelta.")
    return solicitud


def aprobar_solicitud(solicitud_id, resolutor_id):
    """Aprueba: las piezas pasan del stock comunitario al equipo y queda registro."""
    with transaccion() as db:
        solicitud = _obtener_solicitud_pendiente(db, solicitud_id)

        # Se vuelve a comprobar el stock: pudo cambiar desde que el alumno pidió.
        disponible = _disponible(db, solicitud["pieza_id"])
        if disponible is None or disponible < solicitud["cantidad"]:
            raise ValueError("Ya no hay suficientes piezas disponibles para aprobar esta solicitud.")

        _sumar_a_equipo(db, solicitud["pieza_id"], solicitud["equipo_id"], solicitud["cantidad"])
        db.execute(
            "UPDATE solicitudes SET estado = 'aprobada', resuelto_por_id = ?, "
            "fecha_resolucion = datetime('now', 'localtime') WHERE id = ?", (resolutor_id, solicitud_id)
        )
        _anotar_historial(db, "aprobacion", resolutor_id, equipo_id=solicitud["equipo_id"],
                          pieza_id=solicitud["pieza_id"], cantidad=solicitud["cantidad"])
        # Etapa de necesidades: aquí también se descontará la necesidad del equipo.


def rechazar_solicitud(solicitud_id, resolutor_id, motivo):
    with transaccion() as db:
        solicitud = _obtener_solicitud_pendiente(db, solicitud_id)
        db.execute(
            "UPDATE solicitudes SET estado = 'rechazada', resuelto_por_id = ?, "
            "fecha_resolucion = datetime('now', 'localtime'), motivo_rechazo = ? WHERE id = ?",
            (resolutor_id, motivo, solicitud_id),
        )
        _anotar_historial(db, "rechazo", resolutor_id, equipo_id=solicitud["equipo_id"],
                          pieza_id=solicitud["pieza_id"], cantidad=solicitud["cantidad"], motivo=motivo)


# ===========================================================================
# RETIROS DEL INVENTARIO
# ===========================================================================
def retirar_unidades(pieza_id, cantidad, equipo_id, usuario_id, motivo=None):
    """Retira unidades definitivamente del inventario.

    equipo_id=None -> retira del stock comunitario.
    equipo_id=<id> -> retira de las unidades asignadas a ese equipo.
    En ambos casos disminuye cantidad_total y deja una entrada de historial.
    """
    if cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor que cero.")

    with transaccion() as db:
        pieza = db.execute(
            "SELECT id, nombre, cantidad_total, activa FROM piezas WHERE id = ?", (pieza_id,)
        ).fetchone()
        if pieza is None or not pieza["activa"]:
            raise ValueError("La pieza no existe o ya no está activa.")

        if equipo_id is None:
            asignado = db.execute(
                "SELECT COALESCE(SUM(cantidad), 0) AS n FROM asignaciones WHERE pieza_id = ?",
                (pieza_id,),
            ).fetchone()["n"]
            stock = pieza["cantidad_total"] - asignado
            if cantidad > stock:
                raise ValueError(f"Solo hay {stock} unidad(es) en el stock comunitario para retirar.")
            equipo_historial = None
        else:
            asignacion = db.execute(
                "SELECT cantidad FROM asignaciones WHERE pieza_id = ? AND equipo_id = ?",
                (pieza_id, equipo_id),
            ).fetchone()
            del_equipo = 0 if asignacion is None else asignacion["cantidad"]
            if cantidad > del_equipo:
                raise ValueError(f"Ese equipo solo tiene {del_equipo} unidad(es) de esta pieza.")

            nueva = del_equipo - cantidad
            if nueva == 0:
                db.execute(
                    "DELETE FROM asignaciones WHERE pieza_id = ? AND equipo_id = ?",
                    (pieza_id, equipo_id),
                )
            else:
                db.execute(
                    "UPDATE asignaciones SET cantidad = ? WHERE pieza_id = ? AND equipo_id = ?",
                    (nueva, pieza_id, equipo_id),
                )
            equipo_historial = equipo_id

        db.execute(
            "UPDATE piezas SET cantidad_total = cantidad_total - ? WHERE id = ?",
            (cantidad, pieza_id),
        )
        _anotar_historial(
            db, "unidades_retiradas", usuario_id, equipo_id=equipo_historial,
            pieza_id=pieza_id, cantidad=cantidad,
            motivo=motivo or "Retiro definitivo de unidades del inventario.",
        )


def eliminar_pieza_completa(pieza_id, usuario_id, motivo=None):
    """Desactiva una ficha y retira todas sus unidades.

    Se permite únicamente cuando ningún equipo tiene unidades asignadas.
    Así no se ocultan accidentalmente piezas que siguen físicamente en un equipo.
    El historial conserva la existencia de la pieza y la cantidad que tenía.
    """
    with transaccion() as db:
        pieza = db.execute(
            "SELECT id, nombre, cantidad_total, activa FROM piezas WHERE id = ?", (pieza_id,)
        ).fetchone()
        if pieza is None or not pieza["activa"]:
            raise ValueError("La pieza no existe o ya no está activa.")

        asignado = db.execute(
            "SELECT COALESCE(SUM(cantidad), 0) AS n FROM asignaciones WHERE pieza_id = ?",
            (pieza_id,),
        ).fetchone()["n"]
        if asignado > 0:
            raise ValueError(
                "No se puede eliminar por completo porque todavía hay unidades asignadas a equipos. "
                "Devuélvelas al stock primero o retíralas del equipo."
            )

        db.execute("UPDATE piezas SET cantidad_total = 0, activa = 0 WHERE id = ?", (pieza_id,))
        _anotar_historial(
            db, "pieza_eliminada", usuario_id, pieza_id=pieza_id, cantidad=pieza["cantidad_total"],
            motivo=motivo or "Pieza retirada por completo del inventario.",
        )


# ===========================================================================
# DEVOLUCIONES
# ===========================================================================
def devolver_al_stock(pieza_id, equipo_id, cantidad, usuario_id):
    """Mueve piezas físicas del inventario de un equipo al stock comunitario.
    La cantidad_total NO cambia: solo disminuye la asignación del equipo.
    """
    if cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor que cero.")
    with transaccion() as db:
        pieza = db.execute(
            "SELECT id, nombre FROM piezas WHERE id = ? AND activa = 1", (pieza_id,)
        ).fetchone()
        if pieza is None:
            raise ValueError("La pieza no existe o ya no está activa.")
        asignada = db.execute(
            "SELECT cantidad FROM asignaciones WHERE pieza_id = ? AND equipo_id = ?",
            (pieza_id, equipo_id),
        ).fetchone()
        disponible_equipo = 0 if asignada is None else asignada["cantidad"]
        if cantidad > disponible_equipo:
            raise ValueError(
                f"El equipo solo tiene {disponible_equipo} unidad(es) de esta pieza para devolver."
            )

        nueva_cantidad = disponible_equipo - cantidad
        if nueva_cantidad == 0:
            db.execute(
                "DELETE FROM asignaciones WHERE pieza_id = ? AND equipo_id = ?",
                (pieza_id, equipo_id),
            )
        else:
            db.execute(
                "UPDATE asignaciones SET cantidad = ? WHERE pieza_id = ? AND equipo_id = ?",
                (nueva_cantidad, pieza_id, equipo_id),
            )
        _anotar_historial(
            db, "devolucion", usuario_id, equipo_id=equipo_id,
            pieza_id=pieza_id, cantidad=cantidad, motivo="Regreso de piezas al stock comunitario",
        )


# ===========================================================================
# SOLICITUDES DE COMPRA
# ===========================================================================
def crear_solicitud_compra(datos, equipo_id, solicitante_id):
    """Crea una solicitud de compra. Puede apuntar a una pieza existente o ser una pieza nueva."""
    if datos["cantidad"] <= 0:
        raise ValueError("La cantidad debe ser mayor que cero.")
    with transaccion() as db:
        pieza_id = datos.get("pieza_id")
        if pieza_id is not None:
            pieza = db.execute(
                "SELECT * FROM piezas WHERE id = ? AND activa = 1", (pieza_id,)
            ).fetchone()
            if pieza is None:
                raise ValueError("La pieza seleccionada no existe o ya no está activa.")
            pendiente = db.execute(
                "SELECT COALESCE(SUM(cantidad), 0) AS n FROM solicitudes_compra "
                "WHERE equipo_id = ? AND pieza_id = ? AND estado IN ('pendiente', 'aprobada', 'comprada')",
                (equipo_id, pieza_id),
            ).fetchone()["n"]
            if pendiente:
                raise ValueError("Tu equipo ya tiene una solicitud de compra activa para esta pieza.")

        cursor = db.execute(
            "INSERT INTO solicitudes_compra "
            "(equipo_id, solicitante_id, pieza_id, nombre, marca_id, sku, categoria_id, cantidad, "
            "costo_unitario_estimado, link, motivo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                equipo_id, solicitante_id, pieza_id, datos["nombre"], datos.get("marca_id"),
                datos.get("sku") or None, datos.get("categoria_id"), datos["cantidad"],
                datos.get("costo_unitario_estimado", 0), datos.get("link") or None,
                datos.get("motivo") or None,
            ),
        )
        _anotar_historial(
            db, "solicitud_compra", solicitante_id, equipo_id=equipo_id,
            pieza_id=pieza_id, cantidad=datos["cantidad"], motivo=datos.get("motivo"),
        )
    return cursor.lastrowid


def _obtener_solicitud_compra_activa(db, solicitud_id):
    fila = db.execute("SELECT * FROM solicitudes_compra WHERE id = ?", (solicitud_id,)).fetchone()
    if fila is None:
        raise ValueError("La solicitud de compra no existe.")
    return fila


def resolver_solicitud_compra(solicitud_id, accion, resolutor_id, motivo=None):
    """Aprueba o rechaza una solicitud pendiente."""
    with transaccion() as db:
        solicitud = _obtener_solicitud_compra_activa(db, solicitud_id)
        if solicitud["estado"] != "pendiente":
            raise ValueError("La solicitud de compra ya fue resuelta.")
        if accion == "aprobar":
            estado = "aprobada"
            tipo_historial = "compra_aprobada"
            motivo_final = None
        elif accion == "rechazar":
            estado = "rechazada"
            tipo_historial = "compra_rechazada"
            motivo_final = (motivo or "Sin motivo indicado.").strip()
        else:
            raise ValueError("Acción no válida.")
        db.execute(
            "UPDATE solicitudes_compra SET estado = ?, resuelto_por_id = ?, "
            "fecha_resolucion = datetime('now', 'localtime'), motivo_rechazo = ? WHERE id = ?",
            (estado, resolutor_id, motivo_final, solicitud_id),
        )
        _anotar_historial(
            db, tipo_historial, resolutor_id, equipo_id=solicitud["equipo_id"],
            pieza_id=solicitud["pieza_id"], cantidad=solicitud["cantidad"], motivo=motivo_final,
        )


def marcar_compra_realizada(solicitud_id, usuario_id):
    """Marca que la compra ya se realizó, pero todavía no ha llegado al taller."""
    with transaccion() as db:
        solicitud = _obtener_solicitud_compra_activa(db, solicitud_id)
        if solicitud["estado"] != "aprobada":
            raise ValueError("Solo se puede marcar como comprada una solicitud aprobada.")
        db.execute(
            "UPDATE solicitudes_compra SET estado = 'comprada', fecha_compra = datetime('now', 'localtime') "
            "WHERE id = ?", (solicitud_id,)
        )
        _anotar_historial(
            db, "compra_realizada", usuario_id, equipo_id=solicitud["equipo_id"],
            pieza_id=solicitud["pieza_id"], cantidad=solicitud["cantidad"],
        )


def marcar_compra_recibida(solicitud_id, usuario_id):
    """Recibe físicamente la compra y la refleja de inmediato en SQLite.

    - Si la solicitud ya apuntaba a una pieza existente, aumenta cantidad_total y
      asigna las nuevas unidades al equipo solicitante.
    - Si era una pieza nueva, crea la ficha en piezas, crea la asignación al equipo
      solicitante y enlaza la solicitud con la pieza recién creada.
    - También crea el registro histórico de compra recibida.
    """
    with transaccion() as db:
        solicitud = _obtener_solicitud_compra_activa(db, solicitud_id)
        if solicitud["estado"] != "comprada":
            raise ValueError("Primero debes marcar la compra como realizada.")

        pieza_id = solicitud["pieza_id"]

        # Si era una solicitud de una pieza nueva y mientras tanto alguien ya creó
        # la misma pieza con ese SKU, reutilizamos esa ficha en lugar de duplicarla.
        if pieza_id is None and solicitud["sku"]:
            existente = db.execute(
                "SELECT id, activa FROM piezas WHERE sku = ? COLLATE NOCASE",
                (solicitud["sku"],),
            ).fetchone()
            if existente is not None:
                if not existente["activa"]:
                    raise ValueError("Existe una pieza inactiva con ese SKU. Reactívala o corrige el SKU antes de recibir la compra.")
                pieza_id = existente["id"]

        if pieza_id is not None:
            pieza = db.execute(
                "SELECT id, activa FROM piezas WHERE id = ?", (pieza_id,)
            ).fetchone()
            if pieza is None or not pieza["activa"]:
                raise ValueError("La pieza vinculada ya no existe o está inactiva.")

            db.execute(
                "UPDATE piezas SET cantidad_total = cantidad_total + ? WHERE id = ?",
                (solicitud["cantidad"], pieza_id),
            )
            _sumar_a_equipo(db, pieza_id, solicitud["equipo_id"], solicitud["cantidad"])

            # Mantiene la trazabilidad completa de la compra.
            db.execute(
                "INSERT INTO compras (pieza_id, cantidad, estado, marcado_por_id, fecha_recepcion) "
                "VALUES (?, ?, 'recibido', ?, datetime('now', 'localtime'))",
                (pieza_id, solicitud["cantidad"], usuario_id),
            )
        else:
            # Pieza completamente nueva: queda registrada en el inventario en este mismo
            # commit. La ubicación se puede definir después desde Editar pieza.
            if solicitud["sku"]:
                duplicada = db.execute(
                    "SELECT id FROM piezas WHERE sku = ? COLLATE NOCASE",
                    (solicitud["sku"],),
                ).fetchone()
                if duplicada is not None:
                    raise ValueError("El SKU ya existe. Recarga la solicitud y vuelve a intentar recibirla.")

            cursor = db.execute(
                "INSERT INTO piezas "
                "(nombre, marca_id, sku, categoria_id, cantidad_total, costo_unitario, imagen, link, ubicacion_id) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL)",
                (
                    solicitud["nombre"],
                    solicitud["marca_id"],
                    solicitud["sku"] or None,
                    solicitud["categoria_id"],
                    solicitud["cantidad"],
                    solicitud["costo_unitario_estimado"],
                    solicitud["link"] or None,
                ),
            )
            pieza_id = cursor.lastrowid
            _sumar_a_equipo(db, pieza_id, solicitud["equipo_id"], solicitud["cantidad"])

            db.execute(
                "INSERT INTO compras (pieza_id, cantidad, estado, marcado_por_id, fecha_recepcion) "
                "VALUES (?, ?, 'recibido', ?, datetime('now', 'localtime'))",
                (pieza_id, solicitud["cantidad"], usuario_id),
            )
            _anotar_historial(
                db, "pieza_nueva", usuario_id, equipo_id=solicitud["equipo_id"],
                pieza_id=pieza_id, cantidad=solicitud["cantidad"],
                motivo="Pieza creada automáticamente al recibir una solicitud de compra.",
            )

        db.execute(
            "UPDATE solicitudes_compra SET pieza_id = ?, estado = 'recibida', "
            "fecha_recepcion = datetime('now', 'localtime') WHERE id = ?",
            (pieza_id, solicitud_id),
        )
        _anotar_historial(
            db, "compra_recibida", usuario_id, equipo_id=solicitud["equipo_id"],
            pieza_id=pieza_id, cantidad=solicitud["cantidad"],
            motivo="Compra recibida y reflejada en el inventario.",
        )

