from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from config import DEFAULT_IVA
from db import db, obtener_conexion
from seguridad import formatear_mxn, requerir_rol
from services.ws import avisar

router = APIRouter(prefix="/api/ordenes", tags=["Cotizaciones"])


class ItemCotizacion(BaseModel):
    descripcion: str = Field(..., min_length=1)
    tipo: str = "refaccion"  # 'refaccion' | 'mano_obra'
    cantidad: int = Field(1, ge=1)
    precio: float = Field(..., ge=0)


class CotizacionPayload(BaseModel):
    items: list[ItemCotizacion]
    iva_porcentaje: float = Field(default=DEFAULT_IVA, ge=0, le=100)


def _normalizar_entrada(datos: CotizacionPayload | list[ItemCotizacion]) -> tuple[list[ItemCotizacion], float]:
    if isinstance(datos, list):
        return datos, float(DEFAULT_IVA)
    return datos.items, float(datos.iva_porcentaje)


@router.post("/{folio}/cotizacion")
@router.put("/{folio}/cotizacion")
async def guardar_o_editar_cotizacion(
    folio: str,
    datos: CotizacionPayload | list[ItemCotizacion] = Body(...),
    u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico")),
):
    """
    Crea una nueva cotización o actualiza la cotización pendiente existente (incrementando su versión).
    Calcula subtotal, IVA configurable (16% por defecto) y total en MXN.
    """
    items, iva_pct = _normalizar_entrada(datos)
    if not items:
        raise HTTPException(422, "La cotización debe incluir al menos un concepto")

    for it in items:
        if it.tipo not in ("refaccion", "mano_obra"):
            raise HTTPException(422, "El tipo de concepto debe ser 'refaccion' o 'mano_obra'")

    o = db("SELECT id, estatus FROM ordenes WHERE folio=%s", (folio,), "one")
    if not o:
        raise HTTPException(404, "Folio no encontrado")

    uid = int(u.get("id") or u["sub"])
    subtotal = round(sum(float(i.cantidad) * float(i.precio) for i in items), 2)
    iva_monto = round(subtotal * (iva_pct / 100.0), 2)
    total = round(subtotal + iva_monto, 2)

    with obtener_conexion() as c:
        pendiente = c.execute(
            """SELECT id, version FROM cotizaciones
               WHERE orden_id=%s AND estatus='pendiente'
               ORDER BY id DESC LIMIT 1""",
            (o["id"],),
        ).fetchone()

        if pendiente:
            nueva_version = int(pendiente.get("version") or 1) + 1
            cot_id = pendiente["id"]
            c.execute(
                """UPDATE cotizaciones
                   SET iva_porcentaje=%s, subtotal=%s, iva_monto=%s, total=%s,
                       version=%s, actualizada=now()
                   WHERE id=%s""",
                (iva_pct, subtotal, iva_monto, total, nueva_version, cot_id),
            )
            c.execute("DELETE FROM cotizacion_items WHERE cotizacion_id=%s", (cot_id,))
            nota_hist = f"Cotización actualizada (v{nueva_version}) — Total: {formatear_mxn(total)} MXN"
        else:
            nueva_version = 1
            cot = c.execute(
                """INSERT INTO cotizaciones(orden_id, estatus, iva_porcentaje, subtotal, iva_monto, total, version)
                   VALUES(%s, 'pendiente', %s, %s, %s, %s, 1)
                   RETURNING id""",
                (o["id"], iva_pct, subtotal, iva_monto, total),
            ).fetchone()
            cot_id = cot["id"]
            nota_hist = f"Cotización enviada (v1) — Total: {formatear_mxn(total)} MXN"

        for i in items:
            c.execute(
                """INSERT INTO cotizacion_items(cotizacion_id, descripcion, tipo, cantidad, precio)
                   VALUES(%s, %s, %s, %s, %s)""",
                (cot_id, i.descripcion.strip(), i.tipo, i.cantidad, i.precio),
            )

        c.execute("UPDATE ordenes SET estatus='Esperando aprobación' WHERE id=%s", (o["id"],))
        c.execute(
            "INSERT INTO historial_estatus(orden_id, estatus, nota, usuario_id) VALUES(%s, 'Esperando aprobación', %s, %s)",
            (o["id"], nota_hist, uid),
        )

    await avisar(folio)
    return {
        "ok": True,
        "cotizacion_id": cot_id,
        "version": nueva_version,
        "subtotal": subtotal,
        "iva_porcentaje": iva_pct,
        "iva_monto": iva_monto,
        "total": total,
        "total_fmt": formatear_mxn(total),
    }
