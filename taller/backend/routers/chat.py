from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from db import db
from seguridad import requerir_rol
from services import storage as storage_mod
from services.ws import avisar

router = APIRouter(prefix="/api", tags=["Chat y Respuestas Rápidas"])


class MsgInput(BaseModel):
    texto: str = ""
    archivo_url: str | None = None


class RespuestaRapidaInput(BaseModel):
    titulo: str = Field(..., min_length=2)
    texto: str = Field(..., min_length=2)
    activo: bool = True
    orden: int = 0


@router.post("/ordenes/{folio}/mensajes")
async def msg_tecnico(
    folio: str,
    d: MsgInput,
    u: dict = Depends(requerir_rol("admin", "tecnico", "recepcion")),
):
    if not d.texto.strip() and not d.archivo_url:
        raise HTTPException(422, "El mensaje no puede estar vacío")
    o = db("SELECT id FROM ordenes WHERE folio=%s", (folio,), "one")
    if not o:
        raise HTTPException(404, "Folio no encontrado")
    db(
        "INSERT INTO mensajes(orden_id, emisor, texto, archivo_url) VALUES(%s, 'tecnico', %s, %s)",
        (o["id"], d.texto.strip(), d.archivo_url),
        fetch=None,
    )
    await avisar(folio)
    return {"ok": True}


@router.post("/ordenes/{folio}/mensajes/foto")
async def msg_tecnico_foto(
    folio: str,
    texto: str = Form(""),
    archivo: UploadFile = File(...),
    u: dict = Depends(requerir_rol("admin", "tecnico", "recepcion")),
):
    o = db("SELECT id FROM ordenes WHERE folio=%s", (folio,), "one")
    if not o:
        raise HTTPException(404, "Folio no encontrado")
    url = await storage_mod.procesar_y_guardar_imagen(archivo, f"chat/{folio}")
    db(
        "INSERT INTO mensajes(orden_id, emisor, texto, archivo_url) VALUES(%s, 'tecnico', %s, %s)",
        (o["id"], texto.strip(), url),
        fetch=None,
    )
    await avisar(folio)
    return {"ok": True, "archivo_url": url}


@router.post("/ordenes/{folio}/mensajes/leer")
async def marcar_mensajes_leidos_tecnico(
    folio: str,
    u: dict = Depends(requerir_rol("admin", "tecnico", "recepcion")),
):
    o = db("SELECT id FROM ordenes WHERE folio=%s", (folio,), "one")
    if not o:
        raise HTTPException(404, "Folio no encontrado")
    db(
        """UPDATE mensajes
           SET leido_por_tecnico = TRUE
           WHERE orden_id = %s AND emisor = 'cliente' AND COALESCE(leido_por_tecnico, FALSE) = FALSE""",
        (o["id"],),
        fetch=None,
    )
    return {"ok": True}


# ---------- Respuestas rápidas predefinidas (Bloque 2) ----------
@router.get("/respuestas-rapidas")
def listar_respuestas_rapidas(
    todas: bool = False,
    u: dict = Depends(requerir_rol("admin", "tecnico", "recepcion")),
):
    if todas and u.get("rol") == "admin":
        return db("SELECT id, titulo, texto, activo, orden FROM respuestas_rapidas ORDER BY orden ASC, id ASC")
    return db(
        "SELECT id, titulo, texto, activo, orden FROM respuestas_rapidas WHERE activo = TRUE ORDER BY orden ASC, id ASC"
    )


@router.post("/respuestas-rapidas")
def crear_respuesta_rapida(
    d: RespuestaRapidaInput,
    u: dict = Depends(requerir_rol("admin")),
):
    return db(
        """INSERT INTO respuestas_rapidas(titulo, texto, activo, orden)
           VALUES(%s, %s, %s, %s)
           RETURNING id, titulo, texto, activo, orden""",
        (d.titulo.strip(), d.texto.strip(), d.activo, d.orden),
        "one",
    )


@router.put("/respuestas-rapidas/{rr_id}")
def editar_respuesta_rapida(
    rr_id: int,
    d: RespuestaRapidaInput,
    u: dict = Depends(requerir_rol("admin")),
):
    actualizada = db(
        """UPDATE respuestas_rapidas
           SET titulo=%s, texto=%s, activo=%s, orden=%s
           WHERE id=%s
           RETURNING id, titulo, texto, activo, orden""",
        (d.titulo.strip(), d.texto.strip(), d.activo, d.orden, rr_id),
        "one",
    )
    if not actualizada:
        raise HTTPException(404, "Respuesta rápida no encontrada")
    return actualizada


@router.delete("/respuestas-rapidas/{rr_id}")
def eliminar_respuesta_rapida(
    rr_id: int,
    u: dict = Depends(requerir_rol("admin")),
):
    eliminada = db("DELETE FROM respuestas_rapidas WHERE id=%s RETURNING id", (rr_id,), "one")
    if not eliminada:
        raise HTTPException(404, "Respuesta rápida no encontrada")
    return {"ok": True}
