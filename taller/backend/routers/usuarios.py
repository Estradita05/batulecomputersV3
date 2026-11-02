from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from config import ROLES_VALIDOS
from db import db
from seguridad import hash_password, requerir_rol

router = APIRouter(prefix="/api/usuarios", tags=["Usuarios y Administración"])


class CrearUsuarioInput(BaseModel):
    nombre: str = Field(..., min_length=2)
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    rol: str = "tecnico"
    forzar_cambio_password: bool = True


class EditarUsuarioInput(BaseModel):
    nombre: str | None = None
    email: str | None = None
    rol: str | None = None
    activo: bool | None = None


class ResetPasswordInput(BaseModel):
    password_nuevo: str = Field(..., min_length=6)
    forzar_cambio: bool = True


@router.get("")
def listar_usuarios(u: dict = Depends(requerir_rol("admin"))):
    return db(
        """SELECT id, nombre, email, rol, activo, forzar_cambio_password, creado
           FROM usuarios
           ORDER BY id ASC"""
    )


@router.get("/tecnicos")
def listar_tecnicos_activos(u: dict = Depends(requerir_rol("admin", "recepcion", "tecnico"))):
    """Devuelve usuarios activos que pueden atender órdenes (técnicos y administradores)."""
    return db(
        """SELECT id, nombre, rol
           FROM usuarios
           WHERE activo = TRUE AND rol IN ('tecnico', 'admin')
           ORDER BY nombre ASC"""
    )


@router.post("")
def crear_usuario(d: CrearUsuarioInput, u: dict = Depends(requerir_rol("admin"))):
    rol = d.rol.strip().lower()
    if rol not in ROLES_VALIDOS:
        raise HTTPException(422, f"Rol inválido. Debe ser uno de: {', '.join(ROLES_VALIDOS)}")
    email = d.email.strip().lower()
    if db("SELECT 1 FROM usuarios WHERE lower(email)=%s", (email,), "one"):
        raise HTTPException(409, "Ya existe un usuario registrado con ese correo electrónico")
    pw_hash = hash_password(d.password.strip())
    creado = db(
        """INSERT INTO usuarios(nombre, email, password_hash, rol, activo, forzar_cambio_password)
           VALUES(%s, %s, %s, %s, TRUE, %s)
           RETURNING id, nombre, email, rol, activo, forzar_cambio_password, creado""",
        (d.nombre.strip(), email, pw_hash, rol, d.forzar_cambio_password),
        "one",
    )
    return creado


@router.patch("/{user_id}")
def editar_usuario(user_id: int, d: EditarUsuarioInput, u: dict = Depends(requerir_rol("admin"))):
    actual = db("SELECT * FROM usuarios WHERE id=%s", (user_id,), "one")
    if not actual:
        raise HTTPException(404, "Usuario no encontrado")

    mi_id = int(u.get("id") or u["sub"])
    if user_id == mi_id and d.activo is False:
        raise HTTPException(400, "No puedes desactivar tu propia cuenta de administrador")
    if user_id == mi_id and d.rol is not None and d.rol != "admin":
        raise HTTPException(400, "No puedes quitarte el rol de administrador a ti mismo")

    nuevo_nombre = d.nombre.strip() if d.nombre is not None else actual["nombre"]
    nuevo_email = d.email.strip().lower() if d.email is not None else actual["email"]
    nuevo_rol = d.rol.strip().lower() if d.rol is not None else actual["rol"]
    nuevo_activo = d.activo if d.activo is not None else actual["activo"]

    if nuevo_rol not in ROLES_VALIDOS:
        raise HTTPException(422, f"Rol inválido. Debe ser uno de: {', '.join(ROLES_VALIDOS)}")

    dup = db("SELECT id FROM usuarios WHERE lower(email)=%s AND id<>%s", (nuevo_email, user_id), "one")
    if dup:
        raise HTTPException(409, "Otro usuario ya utiliza ese correo electrónico")

    actualizado = db(
        """UPDATE usuarios
           SET nombre=%s, email=%s, rol=%s, activo=%s
           WHERE id=%s
           RETURNING id, nombre, email, rol, activo, forzar_cambio_password, creado""",
        (nuevo_nombre, nuevo_email, nuevo_rol, nuevo_activo, user_id),
        "one",
    )
    return actualizado


@router.post("/{user_id}/reset-password")
def restablecer_password_usuario(
    user_id: int, d: ResetPasswordInput, u: dict = Depends(requerir_rol("admin"))
):
    actual = db("SELECT id FROM usuarios WHERE id=%s", (user_id,), "one")
    if not actual:
        raise HTTPException(404, "Usuario no encontrado")
    pw_hash = hash_password(d.password_nuevo.strip())
    db(
        "UPDATE usuarios SET password_hash=%s, forzar_cambio_password=%s WHERE id=%s",
        (pw_hash, d.forzar_cambio, user_id),
        fetch=None,
    )
    return {"ok": True, "mensaje": "Contraseña restablecida correctamente"}
