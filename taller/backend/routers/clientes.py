from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import db, obtener_conexion
from seguridad import normalizar_telefono, requerir_rol

router = APIRouter(prefix="/api/clientes", tags=["Clientes"])


class CrearClienteInput(BaseModel):
    nombre: str = Field(..., min_length=2)
    telefono: str
    correo: str | None = None


class EditarClienteInput(BaseModel):
    nombre: str | None = None
    telefono: str | None = None
    correo: str | None = None


@router.get("")
def listar_clientes(
    q: str | None = None,
    u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico")),
):
    sql = """SELECT c.id,
                    COALESCE(c.numero_cliente, 'CLI-' || lpad(c.id::text, 5, '0')) AS numero_cliente,
                    c.nombre,
                    c.telefono,
                    c.correo,
                    c.creado,
                    (SELECT COUNT(*) FROM ordenes o JOIN equipos e ON e.id = o.equipo_id WHERE e.cliente_id = c.id) AS total_ordenes
             FROM clientes c"""
    params: list = []

    if q and q.strip():
        texto = q.strip()
        patron = f"%{texto}%"
        tel_limpio = normalizar_telefono(texto)
        if tel_limpio and len(tel_limpio) >= 3:
            sql += """ WHERE (
                COALESCE(c.numero_cliente, '') ILIKE %s
                OR c.nombre ILIKE %s
                OR c.telefono ILIKE %s
                OR COALESCE(c.correo, '') ILIKE %s
            )"""
            params.extend([patron, patron, f"%{tel_limpio}%", patron])
        else:
            sql += """ WHERE (
                COALESCE(c.numero_cliente, '') ILIKE %s
                OR c.nombre ILIKE %s
                OR c.telefono ILIKE %s
                OR COALESCE(c.correo, '') ILIKE %s
            )"""
            params.extend([patron, patron, patron, patron])

    sql += " ORDER BY c.id DESC LIMIT 300"
    return db(sql, tuple(params)) or []


@router.get("/por-telefono/{telefono}")
def buscar_cliente_por_telefono(
    telefono: str,
    u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico")),
):
    t = normalizar_telefono(telefono)
    if len(t) < 10:
        raise HTTPException(422, "El teléfono debe tener 10 dígitos")
    cli = db(
        """SELECT id,
                  COALESCE(numero_cliente, 'CLI-' || lpad(id::text, 5, '0')) AS numero_cliente,
                  nombre,
                  telefono,
                  correo,
                  creado
           FROM clientes
           WHERE telefono = %s""",
        (t,),
        "one",
    )
    if not cli:
        raise HTTPException(404, "No existe ningún cliente registrado con ese número de teléfono")
    return cli


@router.get("/{cliente_id}")
def detalle_cliente(
    cliente_id: int,
    u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico")),
):
    cli = db(
        """SELECT id,
                  COALESCE(numero_cliente, 'CLI-' || lpad(id::text, 5, '0')) AS numero_cliente,
                  nombre,
                  telefono,
                  correo,
                  creado
           FROM clientes
           WHERE id = %s""",
        (cliente_id,),
        "one",
    )
    if not cli:
        raise HTTPException(404, "Cliente no encontrado")

    cli["ordenes"] = db(
        """SELECT o.folio, o.estatus, o.fecha_ingreso, o.fecha_estimada, o.falla,
                  e.tipo, e.marca, e.modelo, e.num_serie
           FROM ordenes o
           JOIN equipos e ON e.id = o.equipo_id
           WHERE e.cliente_id = %s
           ORDER BY o.fecha_ingreso DESC""",
        (cliente_id,),
    ) or []
    return cli


@router.post("")
def crear_cliente(
    d: CrearClienteInput,
    u: dict = Depends(requerir_rol("admin", "recepcion")),
):
    nombre = d.nombre.strip()
    if len(nombre) < 2:
        raise HTTPException(422, "El nombre del cliente debe tener al menos 2 caracteres")

    t = normalizar_telefono(d.telefono)
    if len(t) < 10:
        raise HTTPException(422, "El teléfono del cliente debe tener 10 dígitos")

    correo = (d.correo or "").strip() or None

    with obtener_conexion() as c:
        existente = c.execute(
            """SELECT id,
                      COALESCE(numero_cliente, 'CLI-' || lpad(id::text, 5, '0')) AS numero_cliente,
                      nombre,
                      telefono
               FROM clientes
               WHERE telefono = %s""",
            (t,),
        ).fetchone()
        if existente:
            raise HTTPException(
                409,
                f"Ya existe un cliente registrado con el teléfono {t}: {existente['nombre']} ({existente['numero_cliente']}). No se permiten clientes repetidos.",
            )

        n = c.execute("SELECT nextval('cliente_seq') AS n").fetchone()["n"]
        num_cli = f"CLI-{n:05d}"

        nuevo = c.execute(
            """INSERT INTO clientes(numero_cliente, nombre, telefono, correo)
               VALUES(%s, %s, %s, %s)
               RETURNING id, numero_cliente, nombre, telefono, correo, creado""",
            (num_cli, nombre, t, correo),
        ).fetchone()

    nuevo["total_ordenes"] = 0
    return nuevo


@router.put("/{cliente_id}")
@router.patch("/{cliente_id}")
def actualizar_cliente(
    cliente_id: int,
    d: EditarClienteInput,
    u: dict = Depends(requerir_rol("admin", "recepcion")),
):
    actual = db(
        """SELECT id,
                  COALESCE(numero_cliente, 'CLI-' || lpad(id::text, 5, '0')) AS numero_cliente,
                  nombre,
                  telefono,
                  correo
           FROM clientes
           WHERE id = %s""",
        (cliente_id,),
        "one",
    )
    if not actual:
        raise HTTPException(404, "Cliente no encontrado")

    nuevo_nombre = d.nombre.strip() if d.nombre is not None else actual["nombre"]
    if len(nuevo_nombre) < 2:
        raise HTTPException(422, "El nombre del cliente debe tener al menos 2 caracteres")

    nuevo_tel = normalizar_telefono(d.telefono) if d.telefono is not None else actual["telefono"]
    if len(nuevo_tel) < 10:
        raise HTTPException(422, "El teléfono del cliente debe tener 10 dígitos")

    nuevo_correo = (d.correo.strip() or None) if d.correo is not None else actual["correo"]

    with obtener_conexion() as c:
        dup = c.execute(
            """SELECT id,
                      COALESCE(numero_cliente, 'CLI-' || lpad(id::text, 5, '0')) AS numero_cliente,
                      nombre
               FROM clientes
               WHERE telefono = %s AND id <> %s""",
            (nuevo_tel, cliente_id),
        ).fetchone()
        if dup:
            raise HTTPException(
                409,
                f"El teléfono {nuevo_tel} ya pertenece a otro cliente registrado: {dup['nombre']} ({dup['numero_cliente']})",
            )

        actualizado = c.execute(
            """UPDATE clientes
               SET nombre = %s,
                   telefono = %s,
                   correo = %s,
                   numero_cliente = COALESCE(numero_cliente, %s)
               WHERE id = %s
               RETURNING id, numero_cliente, nombre, telefono, correo, creado""",
            (nuevo_nombre, nuevo_tel, nuevo_correo, actual["numero_cliente"], cliente_id),
        ).fetchone()

    return actualizado
