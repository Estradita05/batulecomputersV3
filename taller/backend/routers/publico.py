from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from db import db
from routers.ordenes import obtener_orden_completa
from seguridad import limite_publico, normalizar_telefono
from services import storage as storage_mod
from services.ws import avisar

router = APIRouter(prefix="/api/public", tags=["Consulta Pública Cliente"])


def verificar_propiedad_cliente(folio: str, telefono: str) -> None:
    """Valida estrictamente que el folio exista y coincida con los 10 dígitos del teléfono del titular."""
    tel_limpio = normalizar_telefono(telefono)
    if len(tel_limpio) < 10:
        raise HTTPException(403, "Folio o teléfono incorrecto")
    r = db(
        """SELECT c.telefono
           FROM ordenes o
           JOIN equipos e ON e.id = o.equipo_id
           JOIN clientes c ON c.id = e.cliente_id
           WHERE o.folio = %s""",
        (folio,),
        "one",
    )
    if not r or normalizar_telefono(r["telefono"]) != tel_limpio:
        raise HTTPException(403, "Folio o teléfono incorrecto")


class RespuestaCotizacionInput(BaseModel):
    aprobar: bool


class MsgClienteInput(BaseModel):
    texto: str = ""
    archivo_url: str | None = None


@router.get("/seguimiento/{folio}", dependencies=[Depends(limite_publico)])
def seguimiento_cliente(folio: str, telefono: str):
    verificar_propiedad_cliente(folio, telefono)
    return obtener_orden_completa(folio, incluir_privado=False)


@router.post("/seguimiento/{folio}/cotizacion", dependencies=[Depends(limite_publico)])
async def responder_cotizacion_cliente(
    folio: str,
    telefono: str,
    d: RespuestaCotizacionInput,
    request: Request,
):
    verificar_propiedad_cliente(folio, telefono)
    o = db("SELECT id FROM ordenes WHERE folio=%s", (folio,), "one")
    if not o:
        raise HTTPException(404, "Folio no encontrado")

    ip_cliente = limite_publico.obtener_ip(request)
    decision = "aprobada" if d.aprobar else "rechazada"

    cot = db(
        """UPDATE cotizaciones
           SET estatus=%s, respondida=now(), ip_respuesta=%s
           WHERE id=(
               SELECT id FROM cotizaciones
               WHERE orden_id=%s AND estatus='pendiente'
               ORDER BY id DESC LIMIT 1
           )
           RETURNING id, version""",
        (decision, ip_cliente, o["id"]),
        "one",
    )
    if not cot:
        raise HTTPException(409, "No hay una cotización pendiente de respuesta para esta orden")

    nuevo_estatus = "En reparación" if d.aprobar else "No reparable / Cancelado"
    nota = (
        f"Cotización v{cot.get('version', 1)} {decision} por el cliente (IP: {ip_cliente})"
    )
    db("UPDATE ordenes SET estatus=%s WHERE id=%s", (nuevo_estatus, o["id"]), fetch=None)
    db(
        "INSERT INTO historial_estatus(orden_id, estatus, nota) VALUES(%s, %s, %s)",
        (o["id"], nuevo_estatus, nota),
        fetch=None,
    )
    await avisar(folio)
    return {"ok": True, "estatus_cotizacion": decision, "estatus_orden": nuevo_estatus}


@router.post("/seguimiento/{folio}/mensajes", dependencies=[Depends(limite_publico)])
async def msg_cliente(folio: str, telefono: str, d: MsgClienteInput):
    verificar_propiedad_cliente(folio, telefono)
    if not d.texto.strip() and not d.archivo_url:
        raise HTTPException(422, "El mensaje no puede estar vacío")
    o = db("SELECT id FROM ordenes WHERE folio=%s", (folio,), "one")
    db(
        "INSERT INTO mensajes(orden_id, emisor, texto, archivo_url) VALUES(%s, 'cliente', %s, %s)",
        (o["id"], d.texto.strip(), d.archivo_url),
        fetch=None,
    )
    await avisar(folio)
    return {"ok": True}


@router.post("/seguimiento/{folio}/mensajes/foto", dependencies=[Depends(limite_publico)])
async def msg_cliente_foto(
    folio: str,
    telefono: str,
    texto: str = Form(""),
    archivo: UploadFile = File(...),
):
    verificar_propiedad_cliente(folio, telefono)
    o = db("SELECT id FROM ordenes WHERE folio=%s", (folio,), "one")
    url = await storage_mod.procesar_y_guardar_imagen(archivo, f"chat/{folio}")
    db(
        "INSERT INTO mensajes(orden_id, emisor, texto, archivo_url) VALUES(%s, 'cliente', %s, %s)",
        (o["id"], texto.strip(), url),
        fetch=None,
    )
    await avisar(folio)
    return {"ok": True, "archivo_url": url}
