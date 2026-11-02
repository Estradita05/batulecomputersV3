from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from config import DEFAULT_IVA, ESTATUS, ETAPAS_FOTO
from db import db, obtener_conexion
from seguridad import (
    ahora_mx,
    cifrar_clave_equipo,
    descifrar_clave_equipo,
    formatear_mxn,
    normalizar_telefono,
    requerir_rol,
)
from services import storage as storage_mod
from services.ws import avisar

router = APIRouter(prefix="/api", tags=["Órdenes"])


def obtener_orden_completa(
    folio: str,
    incluir_privado: bool,
    rol_usuario: str = "tecnico",
    marcar_leidos: bool = True,
) -> dict:
    o = db(
        """SELECT o.id, o.folio, o.equipo_id, o.falla, o.accesorios, o.estatus,
                  o.fecha_ingreso, o.fecha_estimada, o.password_equipo, o.password_equipo_cifrado,
                  o.tecnico_id, o.entregado_a, o.fecha_entrega, o.firma_url, o.saldo_confirmado,
                  o.iniciales_tecnicos, o.seccion_equipo, o.seccion_bajos, o.seccion_medios, o.seccion_altos,
                  (o.fecha_estimada IS NOT NULL AND o.fecha_estimada < CURRENT_DATE
                   AND o.estatus NOT IN ('Entregado', 'No reparable / Cancelado')) AS retrasado,
                  COALESCE(NULLIF(o.iniciales_tecnicos, ''), u.nombre) AS tecnico_nombre,
                  e.cliente_id, e.tipo, e.marca, e.modelo, e.num_serie,
                  COALESCE(c.numero_cliente, 'CLI-' || lpad(c.id::text, 5, '0')) AS numero_cliente,
                  c.nombre AS cliente, c.telefono, c.correo
           FROM ordenes o
           JOIN equipos e ON e.id = o.equipo_id
           JOIN clientes c ON c.id = e.cliente_id
           LEFT JOIN usuarios u ON u.id = o.tecnico_id
           WHERE o.folio = %s""",
        (folio,),
        "one",
    )
    if not o:
        raise HTTPException(404, "Folio no encontrado")

    if incluir_privado and marcar_leidos:
        try:
            db(
                """UPDATE mensajes
                   SET leido_por_tecnico = TRUE
                   WHERE orden_id = %s AND emisor = 'cliente' AND COALESCE(leido_por_tecnico, FALSE) = FALSE""",
                (o["id"],),
                fetch=None,
            )
        except Exception:
            pass

    o["historial"] = db(
        """SELECT h.estatus, h.nota, h.fecha, u.nombre AS usuario_nombre
           FROM historial_estatus h
           LEFT JOIN usuarios u ON u.id = h.usuario_id
           WHERE h.orden_id = %s
           ORDER BY h.fecha ASC""",
        (o["id"],),
    )

    cot = db(
        "SELECT * FROM cotizaciones WHERE orden_id = %s ORDER BY id DESC LIMIT 1",
        (o["id"],),
        "one",
    )
    total_cotizado = 0.0
    if cot:
        cot["items"] = db(
            """SELECT id, descripcion, COALESCE(tipo, 'refaccion') AS tipo, cantidad, precio
               FROM cotizacion_items
               WHERE cotizacion_id = %s
               ORDER BY id ASC""",
            (cot["id"],),
        )
        for it in cot["items"]:
            it["precio"] = float(it["precio"])
            it["importe"] = round(float(it["cantidad"]) * float(it["precio"]), 2)
            it["precio_fmt"] = formatear_mxn(it["precio"])
            it["importe_fmt"] = formatear_mxn(it["importe"])

        subtotal_calc = round(sum(i["importe"] for i in cot["items"]), 2)
        iva_pct = float(cot.get("iva_porcentaje") if cot.get("iva_porcentaje") is not None else 16.0)
        iva_monto = round(subtotal_calc * (iva_pct / 100.0), 2)
        total_calc = round(subtotal_calc + iva_monto, 2)

        cot["iva_porcentaje"] = iva_pct
        cot["subtotal"] = float(cot.get("subtotal") or subtotal_calc)
        cot["iva_monto"] = float(cot.get("iva_monto") or iva_monto)
        cot["total"] = float(cot.get("total") or total_calc)
        cot["version"] = int(cot.get("version") or 1)
        cot["subtotal_fmt"] = formatear_mxn(cot["subtotal"])
        cot["iva_monto_fmt"] = formatear_mxn(cot["iva_monto"])
        cot["total_fmt"] = formatear_mxn(cot["total"])
        total_cotizado = cot["total"]
    o["cotizacion"] = cot

    # Pagos / anticipos y cálculo de saldo pendiente (Bloque 3)
    pagos = db(
        """SELECT p.id, p.monto, p.metodo, p.concepto, p.fecha, u.nombre AS usuario_nombre
           FROM pagos_orden p
           LEFT JOIN usuarios u ON u.id = p.usuario_id
           WHERE p.orden_id = %s
           ORDER BY p.fecha ASC""",
        (o["id"],),
    ) or []
    for p in pagos:
        p["monto"] = float(p["monto"])
        p["monto_fmt"] = formatear_mxn(p["monto"])

    total_pagado = round(sum(p["monto"] for p in pagos), 2)
    saldo_pendiente = round(max(0.0, total_cotizado - total_pagado), 2)

    o["pagos"] = pagos
    o["total_cotizado"] = total_cotizado
    o["total_cotizado_fmt"] = formatear_mxn(total_cotizado)
    o["total_pagado"] = total_pagado
    o["total_pagado_fmt"] = formatear_mxn(total_pagado)
    o["saldo_pendiente"] = saldo_pendiente
    o["saldo_pendiente_fmt"] = formatear_mxn(saldo_pendiente)

    o["mensajes"] = db(
        """SELECT id, emisor, texto, archivo_url, leido_por_tecnico, leido_por_cliente, fecha
           FROM mensajes
           WHERE orden_id = %s
           ORDER BY fecha ASC""",
        (o["id"],),
    )

    if incluir_privado:
        o["fotos"] = db(
            """SELECT id, etapa, archivo_url, descripcion, COALESCE(visible_cliente, TRUE) AS visible_cliente, fecha
               FROM fotos_orden
               WHERE orden_id = %s
               ORDER BY fecha ASC""",
            (o["id"],),
        )
        o["bitacora"] = db(
            """SELECT id, usuario_nombre, campo, valor_anterior, valor_nuevo, fecha
               FROM bitacora_ordenes
               WHERE orden_id = %s
               ORDER BY fecha DESC""",
            (o["id"],),
        )
        prev_cliente = db(
            """SELECT o2.folio, o2.estatus, o2.fecha_ingreso, o2.falla,
                      e2.tipo, e2.marca, e2.modelo, e2.num_serie
               FROM ordenes o2
               JOIN equipos e2 ON e2.id = o2.equipo_id
               WHERE e2.cliente_id = %s AND o2.id <> %s
               ORDER BY o2.fecha_ingreso DESC
               LIMIT 10""",
            (o["cliente_id"], o["id"]),
        ) or []

        prev_serie = []
        serie_limpia = (o.get("num_serie") or "").strip()
        if serie_limpia and serie_limpia.upper() not in ("N/A", "S/N", "SIN SERIE", "-"):
            prev_serie = db(
                """SELECT o2.folio, o2.estatus, o2.fecha_ingreso, o2.falla,
                          e2.tipo, e2.marca, e2.modelo, e2.num_serie,
                          c2.nombre AS cliente
                   FROM ordenes o2
                   JOIN equipos e2 ON e2.id = o2.equipo_id
                   JOIN clientes c2 ON c2.id = e2.cliente_id
                   WHERE lower(trim(e2.num_serie)) = lower(%s) AND o2.id <> %s
                   ORDER BY o2.fecha_ingreso DESC
                   LIMIT 10""",
                (serie_limpia, o["id"]),
            ) or []

        o["historial_previo"] = {
            "cliente": prev_cliente,
            "num_serie": prev_serie,
        }

        tiene_clave = bool(o.get("password_equipo_cifrado") or o.get("password_equipo"))
        o["tiene_password_equipo"] = tiene_clave
        o["puede_ver_password"] = rol_usuario in ("admin", "tecnico")
        o["password_equipo"] = "••••••••" if tiene_clave else None
        o.pop("password_equipo_cifrado", None)
    else:
        o["fotos"] = db(
            """SELECT id, etapa, archivo_url, descripcion, fecha
               FROM fotos_orden
               WHERE orden_id = %s AND COALESCE(visible_cliente, TRUE) = TRUE
               ORDER BY fecha ASC""",
            (o["id"],),
        )
        for k in (
            "password_equipo",
            "password_equipo_cifrado",
            "telefono",
            "correo",
            "id",
            "equipo_id",
            "cliente_id",
            "tecnico_id",
        ):
            o.pop(k, None)
        if o.get("cotizacion"):
            o["cotizacion"].pop("ip_respuesta", None)
    return o


class NuevaOrden(BaseModel):
    cliente_id: int | None = None
    numero_cliente: str | None = None
    cliente_nombre: str | None = None
    cliente_telefono: str | None = None
    cliente_correo: str | None = None
    tipo: str
    marca: str | None = None
    modelo: str | None = None
    num_serie: str | None = None
    falla: str = Field(..., min_length=2)
    accesorios: str | None = None
    password_equipo: str | None = None
    fecha_estimada: str | None = None
    tecnico_id: int | None = None
    iniciales_tecnicos: str | None = None
    seccion_equipo: str | None = None
    seccion_bajos: str | None = None
    seccion_medios: str | None = None
    seccion_altos: str | None = None
    # Campos opcionales de presupuesto de entrada y anticipo (modal de recepción)
    mano_obra_inicial: float = 0.0
    refacciones_inicial: float = 0.0
    iva_porcentaje_inicial: float = 0.0
    anticipo_inicial: float = 0.0
    metodo_anticipo: str = "efectivo"


class EditarOrdenInput(BaseModel):
    cliente_nombre: str | None = None
    cliente_telefono: str | None = None
    cliente_correo: str | None = None
    tipo: str | None = None
    marca: str | None = None
    modelo: str | None = None
    num_serie: str | None = None
    falla: str | None = None
    accesorios: str | None = None
    fecha_estimada: str | None = None
    password_equipo: str | None = None
    iniciales_tecnicos: str | None = None
    seccion_equipo: str | None = None
    seccion_bajos: str | None = None
    seccion_medios: str | None = None
    seccion_altos: str | None = None


class CambioEstatus(BaseModel):
    estatus: str
    nota: str | None = None


class AsignarTecnicoInput(BaseModel):
    tecnico_id: int | None = None


class RegistrarPagoInput(BaseModel):
    monto: float = Field(..., gt=0)
    metodo: str = "efectivo"  # 'efectivo' | 'transferencia' | 'tarjeta'
    concepto: str = "Abono / Anticipo"


class EntregarEquipoInput(BaseModel):
    recibido_por: str = Field(..., min_length=2)
    firma_base64: str = Field(..., min_length=20)
    fecha_entrega: str | None = None
    confirmar_saldo_pendiente: bool = False
    nota: str | None = None


class VisibilidadFotoInput(BaseModel):
    visible_cliente: bool


@router.get("/estatus")
def lista_estatus():
    return ESTATUS


@router.get("/dashboard")
def obtener_dashboard(u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico"))):
    filas_est = db("SELECT estatus, COUNT(*) AS cantidad FROM ordenes GROUP BY estatus") or []
    conteo_map = {e: 0 for e in ESTATUS}
    total_ordenes = 0
    for f in filas_est:
        est = f["estatus"]
        cant = int(f["cantidad"])
        conteo_map[est] = cant
        total_ordenes += cant

    retrasados_row = db(
        """SELECT COUNT(*) AS n
           FROM ordenes
           WHERE fecha_estimada IS NOT NULL
             AND fecha_estimada < CURRENT_DATE
             AND estatus NOT IN ('Entregado', 'No reparable / Cancelado')""",
        fetch="one",
    )
    retrasados = int(retrasados_row["n"]) if retrasados_row else 0
    listos_sin_recoger = conteo_map.get("Listo para entrega", 0)
    recibidos_revision = conteo_map.get("Recibido", 0) + conteo_map.get("En diagnóstico", 0)
    en_reparacion = (
        conteo_map.get("En reparación", 0)
        + conteo_map.get("En pruebas", 0)
        + conteo_map.get("Esperando refacción", 0)
    )

    ingresos_row = db(
        """SELECT
             COALESCE((
               SELECT SUM(p.monto)
               FROM pagos_orden p
               WHERE date_trunc('month', p.fecha) = date_trunc('month', now())
             ), 0)
             +
             COALESCE((
               SELECT SUM(c.total)
               FROM cotizaciones c
               WHERE c.estatus = 'aprobada'
                 AND date_trunc('month', COALESCE(c.respondida, c.creada)) = date_trunc('month', now())
                 AND NOT EXISTS (SELECT 1 FROM pagos_orden p2 WHERE p2.orden_id = c.orden_id)
             ), 0) AS ingresos_mes""",
        fetch="one",
    )
    ingresos_mes = round(float(ingresos_row["ingresos_mes"] or 0.0), 2) if ingresos_row else 0.0

    return {
        "total_ordenes": total_ordenes,
        "recibidos_revision": recibidos_revision,
        "en_reparacion": en_reparacion,
        "por_estatus": [{"estatus": e, "cantidad": conteo_map[e]} for e in ESTATUS],
        "retrasados": retrasados,
        "listos_sin_recoger": listos_sin_recoger,
        "ingresos_mes": ingresos_mes,
        "ingresos_mes_fmt": formatear_mxn(ingresos_mes),
    }


@router.post("/ordenes")
def crear_orden(d: NuevaOrden, u: dict = Depends(requerir_rol("admin", "recepcion"))):
    uid = int(u.get("id") or u["sub"])
    tec_id = d.tecnico_id if d.tecnico_id is not None else uid
    clave_cifrada = cifrar_clave_equipo(d.password_equipo)
    anio = ahora_mx().year

    with obtener_conexion() as c:
        cli = None
        if d.cliente_id is not None:
            cli = c.execute(
                """SELECT id,
                          COALESCE(numero_cliente, 'CLI-' || lpad(id::text, 5, '0')) AS numero_cliente,
                          nombre,
                          telefono,
                          correo
                   FROM clientes
                   WHERE id = %s""",
                (d.cliente_id,),
            ).fetchone()
            if not cli:
                raise HTTPException(
                    404,
                    "El cliente seleccionado no está registrado. Primero registra al cliente en el módulo de Clientes.",
                )
        elif d.numero_cliente and d.numero_cliente.strip():
            cli = c.execute(
                """SELECT id,
                          COALESCE(numero_cliente, 'CLI-' || lpad(id::text, 5, '0')) AS numero_cliente,
                          nombre,
                          telefono,
                          correo
                   FROM clientes
                   WHERE upper(trim(numero_cliente)) = upper(trim(%s))""",
                (d.numero_cliente.strip(),),
            ).fetchone()
            if not cli:
                raise HTTPException(
                    404,
                    f"No existe ningún cliente registrado con el número {d.numero_cliente.strip()}",
                )
        else:
            if not d.cliente_telefono:
                raise HTTPException(422, "Debes seleccionar un cliente registrado para crear la orden")
            t = normalizar_telefono(d.cliente_telefono)
            if len(t) < 10:
                raise HTTPException(422, "El teléfono del cliente debe tener 10 dígitos")
            cli = c.execute(
                """SELECT id,
                          COALESCE(numero_cliente, 'CLI-' || lpad(id::text, 5, '0')) AS numero_cliente,
                          nombre,
                          telefono,
                          correo
                   FROM clientes
                   WHERE telefono = %s""",
                (t,),
            ).fetchone()
            if not cli:
                if not d.cliente_nombre or len(d.cliente_nombre.strip()) < 2:
                    raise HTTPException(
                        404,
                        f"El teléfono {t} no pertenece a ningún cliente registrado. Primero regístralo en el módulo de Clientes.",
                    )
                n_cli = c.execute("SELECT nextval('cliente_seq') AS n").fetchone()["n"]
                num_cli = f"CLI-{n_cli:05d}"
                cli = c.execute(
                    """INSERT INTO clientes(numero_cliente, nombre, telefono, correo)
                       VALUES(%s, %s, %s, %s)
                       RETURNING id, numero_cliente, nombre, telefono, correo""",
                    (num_cli, d.cliente_nombre.strip(), t, (d.cliente_correo or "").strip() or None),
                ).fetchone()

        eq = c.execute(
            """INSERT INTO equipos(cliente_id, tipo, marca, modelo, num_serie)
               VALUES(%s, %s, %s, %s, %s)
               RETURNING id""",
            (
                cli["id"],
                d.tipo.strip(),
                (d.marca or "").strip() or None,
                (d.modelo or "").strip() or None,
                (d.num_serie or "").strip() or None,
            ),
        ).fetchone()
        sec_eq = (d.seccion_equipo or "").strip().lower()
        if sec_eq in ("bajos", "bajo"):
            n = c.execute("SELECT nextval('folio_bajos_seq') AS n").fetchone()["n"]
            folio = str(n)
            sec_nombre = "Bajos"
        elif sec_eq in ("medios", "medio"):
            n = c.execute("SELECT nextval('folio_medios_seq') AS n").fetchone()["n"]
            folio = str(n)
            sec_nombre = "Medios"
        elif sec_eq in ("altos", "alto"):
            n = c.execute("SELECT nextval('folio_altos_seq') AS n").fetchone()["n"]
            folio = str(n)
            sec_nombre = "Altos"
        else:
            n = c.execute("SELECT nextval('folio_seq') AS n").fetchone()["n"]
            folio = f"SRV-{anio}-{n:05d}"
            sec_nombre = None

        o = c.execute(
            """INSERT INTO ordenes(
                   folio, equipo_id, falla, accesorios,
                   password_equipo, password_equipo_cifrado,
                   tecnico_id, fecha_estimada,
                   iniciales_tecnicos, seccion_equipo, seccion_bajos, seccion_medios, seccion_altos
               )
               VALUES(%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                folio,
                eq["id"],
                d.falla.strip(),
                (d.accesorios or "").strip() or None,
                clave_cifrada,
                tec_id,
                d.fecha_estimada or None,
                (d.iniciales_tecnicos or "").strip().upper() or None,
                sec_nombre,
                (d.seccion_bajos or "").strip() or None,
                (d.seccion_medios or "").strip() or None,
                (d.seccion_altos or "").strip() or None,
            ),
        ).fetchone()
        c.execute(
            "INSERT INTO historial_estatus(orden_id, estatus, usuario_id) VALUES(%s, 'Recibido', %s)",
            (o["id"], uid),
        )

        # Si en recepción se capturó presupuesto estimado inicial (Mano de obra / Refacciones)
        mo = max(0.0, float(d.mano_obra_inicial or 0.0))
        ref = max(0.0, float(d.refacciones_inicial or 0.0))
        if mo > 0 or ref > 0:
            sub = round(mo + ref, 2)
            iva_pct = max(0.0, float(d.iva_porcentaje_inicial or 0.0))
            iva_m = round(sub * (iva_pct / 100.0), 2)
            tot = round(sub + iva_m, 2)
            cot_row = c.execute(
                """INSERT INTO cotizaciones(orden_id, estatus, iva_porcentaje, subtotal, iva_monto, total, version)
                   VALUES(%s, 'pendiente', %s, %s, %s, %s, 1)
                   RETURNING id""",
                (o["id"], iva_pct, sub, iva_m, tot),
            ).fetchone()
            if mo > 0:
                c.execute(
                    "INSERT INTO cotizacion_items(cotizacion_id, descripcion, tipo, cantidad, precio) VALUES(%s, 'Mano de obra / Servicio', 'mano_obra', 1, %s)",
                    (cot_row["id"], mo),
                )
            if ref > 0:
                c.execute(
                    "INSERT INTO cotizacion_items(cotizacion_id, descripcion, tipo, cantidad, precio) VALUES(%s, 'Refacciones estimadas', 'refaccion', 1, %s)",
                    (cot_row["id"], ref),
                )

        # Si se dejó anticipo inicial al recibir el equipo
        ant = max(0.0, float(d.anticipo_inicial or 0.0))
        if ant > 0:
            met = d.metodo_anticipo if d.metodo_anticipo in ("efectivo", "transferencia", "tarjeta") else "efectivo"
            c.execute(
                """INSERT INTO pagos_orden(orden_id, monto, metodo, concepto, usuario_id)
                   VALUES(%s, %s, %s, 'Anticipo en recepción', %s)""",
                (o["id"], ant, met, uid),
            )

    return {"folio": folio, "cliente_id": cli["id"], "numero_cliente": cli["numero_cliente"]}


@router.get("/ordenes")
def listar_ordenes(
    q: str | None = None,
    estatus: str | None = None,
    tecnico_id: int | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    retrasados: bool = False,
    u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico")),
):
    sql = """SELECT o.folio, o.estatus, o.fecha_ingreso, o.fecha_estimada,
                    o.tecnico_id,
                    o.iniciales_tecnicos, o.seccion_equipo, o.seccion_bajos, o.seccion_medios, o.seccion_altos,
                    COALESCE(NULLIF(o.iniciales_tecnicos, ''), u.nombre, 'Sin asignar') AS tecnico_nombre,
                    (o.password_equipo_cifrado IS NOT NULL OR o.password_equipo IS NOT NULL) AS tiene_password_equipo,
                    (o.fecha_estimada IS NOT NULL AND o.fecha_estimada < CURRENT_DATE
                     AND o.estatus NOT IN ('Entregado', 'No reparable / Cancelado')) AS retrasado,
                    (SELECT COUNT(*) FROM mensajes m
                     WHERE m.orden_id = o.id
                       AND m.emisor = 'cliente'
                       AND COALESCE(m.leido_por_tecnico, FALSE) = FALSE) AS mensajes_no_leidos,
                    (SELECT COUNT(*) FROM fotos_orden fo WHERE fo.orden_id = o.id) AS num_fotos,
                    COALESCE((SELECT c2.total FROM cotizaciones c2 WHERE c2.orden_id = o.id ORDER BY c2.id DESC LIMIT 1), 0) AS total_cotizado,
                    COALESCE((SELECT SUM(po.monto) FROM pagos_orden po WHERE po.orden_id = o.id), 0) AS total_pagado,
                    e.tipo, e.marca, e.modelo, e.num_serie,
                    c.id AS cliente_id,
                    COALESCE(c.numero_cliente, 'CLI-' || lpad(c.id::text, 5, '0')) AS numero_cliente,
                    c.nombre AS cliente, c.telefono
             FROM ordenes o
             JOIN equipos e ON e.id = o.equipo_id
             JOIN clientes c ON c.id = e.cliente_id
             LEFT JOIN usuarios u ON u.id = o.tecnico_id"""

    condiciones = []
    params: list = []

    if q and q.strip():
        patron = f"%{q.strip()}%"
        condiciones.append(
            "(o.folio ILIKE %s OR COALESCE(c.numero_cliente, '') ILIKE %s OR c.nombre ILIKE %s OR c.telefono ILIKE %s OR COALESCE(e.num_serie, '') ILIKE %s OR COALESCE(e.marca, '') ILIKE %s OR COALESCE(e.modelo, '') ILIKE %s)"
        )
        params.extend([patron, patron, patron, patron, patron, patron, patron])

    if estatus and estatus.strip():
        condiciones.append("o.estatus = %s")
        params.append(estatus.strip())

    if tecnico_id is not None:
        condiciones.append("o.tecnico_id = %s")
        params.append(tecnico_id)

    if fecha_desde and fecha_desde.strip():
        condiciones.append("o.fecha_ingreso::date >= %s")
        params.append(fecha_desde.strip())

    if fecha_hasta and fecha_hasta.strip():
        condiciones.append("o.fecha_ingreso::date <= %s")
        params.append(fecha_hasta.strip())

    if retrasados:
        condiciones.append(
            "o.fecha_estimada IS NOT NULL AND o.fecha_estimada < CURRENT_DATE AND o.estatus NOT IN ('Entregado', 'No reparable / Cancelado')"
        )

    if condiciones:
        sql += " WHERE " + " AND ".join(condiciones)

    sql += " ORDER BY o.fecha_ingreso DESC LIMIT 200"
    filas = db(sql, tuple(params)) or []
    for f in filas:
        tc = float(f.get("total_cotizado") or 0.0)
        tp = float(f.get("total_pagado") or 0.0)
        sp = round(max(0.0, tc - tp), 2)
        f["total_cotizado"] = tc
        f["total_pagado"] = tp
        f["saldo_pendiente"] = sp
    return filas


@router.get("/ordenes/{folio}")
def detalle_orden(
    folio: str,
    u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico")),
):
    return obtener_orden_completa(folio, incluir_privado=True, rol_usuario=u.get("rol", "tecnico"))


@router.patch("/ordenes/{folio}/tecnico")
async def asignar_tecnico_orden(
    folio: str,
    d: AsignarTecnicoInput,
    u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico")),
):
    orden = db(
        """SELECT o.id, o.tecnico_id, u.nombre AS tecnico_anterior
           FROM ordenes o
           LEFT JOIN usuarios u ON u.id = o.tecnico_id
           WHERE o.folio = %s""",
        (folio,),
        "one",
    )
    if not orden:
        raise HTTPException(404, "Folio no encontrado")

    nuevo_nombre = "Sin asignar"
    if d.tecnico_id is not None:
        tec = db("SELECT id, nombre, activo FROM usuarios WHERE id=%s", (d.tecnico_id,), "one")
        if not tec or not tec.get("activo", True):
            raise HTTPException(404, "Técnico no encontrado o inactivo")
        nuevo_nombre = tec["nombre"]

    ant_nombre = orden.get("tecnico_anterior") or "Sin asignar"
    uid = int(u.get("id") or u["sub"])
    unombre = u.get("nombre", "Usuario")

    db("UPDATE ordenes SET tecnico_id=%s WHERE id=%s", (d.tecnico_id, orden["id"]), fetch=None)
    if ant_nombre != nuevo_nombre:
        db(
            """INSERT INTO bitacora_ordenes(orden_id, usuario_id, usuario_nombre, campo, valor_anterior, valor_nuevo)
               VALUES(%s, %s, %s, 'Técnico asignado', %s, %s)""",
            (orden["id"], uid, unombre, ant_nombre, nuevo_nombre),
            fetch=None,
        )
    await avisar(folio)
    return {"ok": True, "tecnico_id": d.tecnico_id, "tecnico_nombre": nuevo_nombre}


@router.get("/ordenes/{folio}/password-equipo")
def revelar_password_equipo(
    folio: str,
    u: dict = Depends(requerir_rol("admin", "tecnico")),
):
    fila = db(
        "SELECT password_equipo, password_equipo_cifrado FROM ordenes WHERE folio=%s",
        (folio,),
        "one",
    )
    if not fila:
        raise HTTPException(404, "Folio no encontrado")
    valor = fila.get("password_equipo_cifrado") or fila.get("password_equipo")
    if not valor:
        return {"password_equipo": None}
    return {"password_equipo": descifrar_clave_equipo(valor)}


@router.put("/ordenes/{folio}")
async def editar_orden(
    folio: str,
    d: EditarOrdenInput,
    u: dict = Depends(requerir_rol("admin", "recepcion")),
):
    actual = db(
        """SELECT o.id AS orden_id, o.equipo_id, o.falla, o.accesorios, o.fecha_estimada,
                  o.password_equipo, o.password_equipo_cifrado,
                  o.iniciales_tecnicos, o.seccion_bajos, o.seccion_medios, o.seccion_altos,
                  e.cliente_id, e.tipo, e.marca, e.modelo, e.num_serie,
                  c.nombre AS cliente_nombre, c.telefono AS cliente_telefono, c.correo AS cliente_correo
           FROM ordenes o
           JOIN equipos e ON e.id = o.equipo_id
           JOIN clientes c ON c.id = e.cliente_id
           WHERE o.folio = %s""",
        (folio,),
        "one",
    )
    if not actual:
        raise HTTPException(404, "Folio no encontrado")

    uid = int(u.get("id") or u["sub"])
    unombre = u.get("nombre", "Usuario")
    cambios: list[tuple[str, str, str]] = []

    def normalizar_str(val) -> str:
        if val is None:
            return ""
        return str(val).strip()

    nuevo_cli_nombre = normalizar_str(d.cliente_nombre) if d.cliente_nombre is not None else normalizar_str(actual["cliente_nombre"])
    if not nuevo_cli_nombre:
        raise HTTPException(422, "El nombre del cliente no puede estar vacío")

    nuevo_cli_tel = normalizar_telefono(d.cliente_telefono) if d.cliente_telefono is not None else normalizar_str(actual["cliente_telefono"])
    if len(nuevo_cli_tel) < 10:
        raise HTTPException(422, "El teléfono del cliente debe tener 10 dígitos")

    nuevo_cli_correo = normalizar_str(d.cliente_correo) if d.cliente_correo is not None else normalizar_str(actual["cliente_correo"])

    nuevo_tipo = normalizar_str(d.tipo) if d.tipo is not None else normalizar_str(actual["tipo"])
    nueva_marca = normalizar_str(d.marca) if d.marca is not None else normalizar_str(actual["marca"])
    nuevo_modelo = normalizar_str(d.modelo) if d.modelo is not None else normalizar_str(actual["modelo"])
    nuevo_serie = normalizar_str(d.num_serie) if d.num_serie is not None else normalizar_str(actual["num_serie"])

    nueva_falla = normalizar_str(d.falla) if d.falla is not None else normalizar_str(actual["falla"])
    if not nueva_falla:
        raise HTTPException(422, "La descripción de la falla no puede estar vacía")
    nuevos_acc = normalizar_str(d.accesorios) if d.accesorios is not None else normalizar_str(actual["accesorios"])
    fecha_est_ant = str(actual["fecha_estimada"] or "")
    nueva_fecha_est = normalizar_str(d.fecha_estimada) if d.fecha_estimada is not None else fecha_est_ant

    nuevas_ini = normalizar_str(d.iniciales_tecnicos).upper() if d.iniciales_tecnicos is not None else normalizar_str(actual.get("iniciales_tecnicos"))
    nuevos_bajos = normalizar_str(d.seccion_bajos) if d.seccion_bajos is not None else normalizar_str(actual.get("seccion_bajos"))
    nuevos_medios = normalizar_str(d.seccion_medios) if d.seccion_medios is not None else normalizar_str(actual.get("seccion_medios"))
    nuevos_altos = normalizar_str(d.seccion_altos) if d.seccion_altos is not None else normalizar_str(actual.get("seccion_altos"))

    comparaciones = [
        ("Nombre del cliente", normalizar_str(actual["cliente_nombre"]), nuevo_cli_nombre),
        ("Teléfono del cliente", normalizar_str(actual["cliente_telefono"]), nuevo_cli_tel),
        ("Correo del cliente", normalizar_str(actual["cliente_correo"]), nuevo_cli_correo),
        ("Tipo de equipo", normalizar_str(actual["tipo"]), nuevo_tipo),
        ("Marca", normalizar_str(actual["marca"]), nueva_marca),
        ("Modelo", normalizar_str(actual["modelo"]), nuevo_modelo),
        ("Número de serie", normalizar_str(actual["num_serie"]), nuevo_serie),
        ("Iniciales técnicos recepción", normalizar_str(actual.get("iniciales_tecnicos")), nuevas_ini),
        ("Sección Bajos (Software)", normalizar_str(actual.get("seccion_bajos")), nuevos_bajos),
        ("Sección Medios (Periféricos)", normalizar_str(actual.get("seccion_medios")), nuevos_medios),
        ("Sección Altos (Hardware)", normalizar_str(actual.get("seccion_altos")), nuevos_altos),
        ("Falla reportada", normalizar_str(actual["falla"]), nueva_falla),
        ("Accesorios", normalizar_str(actual["accesorios"]), nuevos_acc),
        ("Fecha estimada", fecha_est_ant, nueva_fecha_est),
    ]
    for etiqueta, ant, nvo in comparaciones:
        if ant != nvo:
            cambios.append((etiqueta, ant or "(vacío)", nvo or "(vacío)"))

    nueva_clave_cifrada = actual["password_equipo_cifrado"]
    if d.password_equipo is not None:
        clave_ant_plana = descifrar_clave_equipo(actual["password_equipo_cifrado"] or actual["password_equipo"]) or ""
        clave_nva_plana = d.password_equipo.strip()
        if clave_nva_plana != clave_ant_plana:
            nueva_clave_cifrada = cifrar_clave_equipo(clave_nva_plana) if clave_nva_plana else None
            cambios.append(("Contraseña del equipo", "[protegida]", "[actualizada]" if clave_nva_plana else "(eliminada)"))

    if not cambios:
        return {"ok": True, "cambios": 0, "mensaje": "No se detectaron cambios"}

    with obtener_conexion() as c:
        dup_tel = c.execute(
            "SELECT id FROM clientes WHERE telefono=%s AND id<>%s",
            (nuevo_cli_tel, actual["cliente_id"]),
        ).fetchone()
        if dup_tel:
            raise HTTPException(409, "Ese número de teléfono ya está asignado a otro cliente")

        c.execute(
            "UPDATE clientes SET nombre=%s, telefono=%s, correo=%s WHERE id=%s",
            (nuevo_cli_nombre, nuevo_cli_tel, nuevo_cli_correo or None, actual["cliente_id"]),
        )
        c.execute(
            "UPDATE equipos SET tipo=%s, marca=%s, modelo=%s, num_serie=%s WHERE id=%s",
            (nuevo_tipo, nueva_marca or None, nuevo_modelo or None, nuevo_serie or None, actual["equipo_id"]),
        )
        c.execute(
            """UPDATE ordenes
               SET falla=%s, accesorios=%s, fecha_estimada=%s,
                   iniciales_tecnicos=%s, seccion_bajos=%s, seccion_medios=%s, seccion_altos=%s,
                   password_equipo=NULL, password_equipo_cifrado=%s
               WHERE id=%s""",
            (
                nueva_falla,
                nuevos_acc or None,
                nueva_fecha_est or None,
                nuevas_ini or None,
                nuevos_bajos or None,
                nuevos_medios or None,
                nuevos_altos or None,
                nueva_clave_cifrada,
                actual["orden_id"],
            ),
        )
        for campo, val_ant, val_nvo in cambios:
            c.execute(
                """INSERT INTO bitacora_ordenes(orden_id, usuario_id, usuario_nombre, campo, valor_anterior, valor_nuevo)
                   VALUES(%s, %s, %s, %s, %s, %s)""",
                (actual["orden_id"], uid, unombre, campo, val_ant, val_nvo),
            )

    await avisar(folio)
    return {"ok": True, "cambios": len(cambios)}


@router.patch("/ordenes/{folio}/estatus")
async def cambiar_estatus(
    folio: str,
    d: CambioEstatus,
    u: dict = Depends(requerir_rol("admin", "tecnico", "recepcion")),
):
    if d.estatus not in ESTATUS:
        raise HTTPException(422, "Estatus inválido")
    if u.get("rol") == "recepcion" and d.estatus not in ("Entregado", "Recibido", "Listo para entrega"):
        raise HTTPException(403, "Recepción solo puede actualizar estatus de recepción o entrega")

    o = db("UPDATE ordenes SET estatus=%s WHERE folio=%s RETURNING id", (d.estatus, folio), "one")
    if not o:
        raise HTTPException(404, "Folio no encontrado")
    uid = int(u.get("id") or u["sub"])
    db(
        "INSERT INTO historial_estatus(orden_id, estatus, nota, usuario_id) VALUES(%s, %s, %s, %s)",
        (o["id"], d.estatus, d.nota, uid),
        fetch=None,
    )
    await avisar(folio)
    return {"ok": True}


# ---------- Bloque 3: Anticipos, Pagos y Flujo de Entrega con Firma ----------
@router.post("/ordenes/{folio}/pagos")
async def registrar_pago_orden(
    folio: str,
    d: RegistrarPagoInput,
    u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico")),
):
    metodo = d.metodo.strip().lower()
    if metodo not in ("efectivo", "transferencia", "tarjeta"):
        raise HTTPException(422, "El método de pago debe ser 'efectivo', 'transferencia' o 'tarjeta'")
    o = db("SELECT id FROM ordenes WHERE folio=%s", (folio,), "one")
    if not o:
        raise HTTPException(404, "Folio no encontrado")

    uid = int(u.get("id") or u["sub"])
    pago = db(
        """INSERT INTO pagos_orden(orden_id, monto, metodo, concepto, usuario_id)
           VALUES(%s, %s, %s, %s, %s)
           RETURNING id, monto, metodo, concepto, fecha""",
        (o["id"], round(float(d.monto), 2), metodo, (d.concepto or "Abono").strip(), uid),
        "one",
    )
    await avisar(folio)
    orden_act = obtener_orden_completa(folio, incluir_privado=True, rol_usuario=u.get("rol", "admin"), marcar_leidos=False)
    return {
        "ok": True,
        "pago": pago,
        "total_cotizado": orden_act["total_cotizado"],
        "total_pagado": orden_act["total_pagado"],
        "saldo_pendiente": orden_act["saldo_pendiente"],
    }


@router.post("/ordenes/{folio}/entregar")
async def entregar_equipo(
    folio: str,
    d: EntregarEquipoInput,
    u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico")),
):
    """
    Flujo de entrega formal (Bloque 3):
    - Verifica saldo pendiente: no permite entregar con saldo sin confirmación explícita (409).
    - Guarda firma de conformidad dibujada en canvas como imagen en el servidor.
    - Registra quién recoge, fecha/hora de entrega y cambia estatus a 'Entregado'.
    """
    orden_act = obtener_orden_completa(folio, incluir_privado=True, rol_usuario=u.get("rol", "admin"), marcar_leidos=False)
    o_row = db("SELECT id FROM ordenes WHERE folio=%s", (folio,), "one")
    saldo = float(orden_act["saldo_pendiente"])

    if saldo > 0.0 and not d.confirmar_saldo_pendiente:
        raise HTTPException(
            409,
            f"El equipo tiene un saldo pendiente de {formatear_mxn(saldo)} MXN. Registra el pago restante o marca la confirmación explícita para autorizar la entrega con saldo.",
        )

    firma_url = storage_mod.guardar_firma_base64(d.firma_base64, folio)
    uid = int(u.get("id") or u["sub"])
    recibe = d.recibido_por.strip()

    with obtener_conexion() as c:
        if d.fecha_entrega and d.fecha_entrega.strip():
            c.execute(
                """UPDATE ordenes
                   SET estatus='Entregado', entregado_a=%s, fecha_entrega=%s, firma_url=%s, saldo_confirmado=%s
                   WHERE id=%s""",
                (recibe, d.fecha_entrega.strip(), firma_url, bool(saldo > 0), o_row["id"]),
            )
        else:
            c.execute(
                """UPDATE ordenes
                   SET estatus='Entregado', entregado_a=%s, fecha_entrega=now(), firma_url=%s, saldo_confirmado=%s
                   WHERE id=%s""",
                (recibe, firma_url, bool(saldo > 0), o_row["id"]),
            )
        nota_hist = f"Entregado a: {recibe}"
        if d.nota and d.nota.strip():
            nota_hist += f" — {d.nota.strip()}"
        if saldo > 0:
            nota_hist += f" (Entregado con saldo pendiente confirmado: {formatear_mxn(saldo)} MXN)"
        c.execute(
            "INSERT INTO historial_estatus(orden_id, estatus, nota, usuario_id) VALUES(%s, 'Entregado', %s, %s)",
            (o_row["id"], nota_hist, uid),
        )

    await avisar(folio)
    return {"ok": True, "folio": folio, "entregado_a": recibe, "firma_url": firma_url, "saldo_pendiente": saldo}


# ---------- Fotos y Evidencia ----------
@router.post("/ordenes/{folio}/fotos")
async def subir_fotos_orden(
    folio: str,
    etapa: str = Form("recepcion"),
    descripcion: str | None = Form(None),
    visible_cliente: bool = Form(True),
    archivos: list[UploadFile] = File(...),
    u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico")),
):
    etapa_limpia = etapa.strip().lower()
    if etapa_limpia not in ETAPAS_FOTO:
        raise HTTPException(422, f"Etapa inválida. Debe ser una de: {', '.join(ETAPAS_FOTO)}")
    o = db("SELECT id FROM ordenes WHERE folio=%s", (folio,), "one")
    if not o:
        raise HTTPException(404, "Folio no encontrado")
    if not archivos:
        raise HTTPException(422, "Debes enviar al menos una imagen")

    uid = int(u.get("id") or u["sub"])
    guardadas = []
    for archivo in archivos:
        url = await storage_mod.procesar_y_guardar_imagen(archivo, f"ordenes/{folio}")
        fila = db(
            """INSERT INTO fotos_orden(orden_id, etapa, archivo_url, descripcion, subido_por)
               VALUES(%s, %s, %s, %s, %s)
               RETURNING id, etapa, archivo_url, descripcion, fecha""",
            (o["id"], etapa_limpia, url, descripcion or None, uid),
            "one",
        )
        if fila:
            fila["visible_cliente"] = bool(visible_cliente)
            if not visible_cliente:
                try:
                    db("UPDATE fotos_orden SET visible_cliente=FALSE WHERE id=%s", (fila["id"],), fetch=None)
                except Exception:
                    pass
        guardadas.append(fila)

    await avisar(folio)
    return {"ok": True, "fotos": guardadas}


@router.patch("/ordenes/{folio}/fotos/{foto_id}")
async def cambiar_visibilidad_foto(
    folio: str,
    foto_id: int,
    d: VisibilidadFotoInput,
    u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico")),
):
    """Permite marcar o desmarcar si una fotografía es visible en el portal público del cliente."""
    o = db("SELECT id FROM ordenes WHERE folio=%s", (folio,), "one")
    if not o:
        raise HTTPException(404, "Folio no encontrado")
    act = db(
        "UPDATE fotos_orden SET visible_cliente=%s WHERE id=%s AND orden_id=%s RETURNING id, visible_cliente",
        (d.visible_cliente, foto_id, o["id"]),
        "one",
    )
    if not act:
        raise HTTPException(404, "Fotografía no encontrada")
    await avisar(folio)
    return {"ok": True, "id": act["id"], "visible_cliente": act["visible_cliente"]}


@router.delete("/ordenes/{folio}/fotos/{foto_id}")
async def eliminar_foto_orden(
    folio: str,
    foto_id: int,
    u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico")),
):
    o = db("SELECT id FROM ordenes WHERE folio=%s", (folio,), "one")
    if not o:
        raise HTTPException(404, "Folio no encontrado")
    foto = db(
        "DELETE FROM fotos_orden WHERE id=%s AND orden_id=%s RETURNING archivo_url",
        (foto_id, o["id"]),
        "one",
    )
    if not foto:
        raise HTTPException(404, "Fotografía no encontrada")
    storage_mod.storage.eliminar(foto["archivo_url"])
    await avisar(folio)
    return {"ok": True}
