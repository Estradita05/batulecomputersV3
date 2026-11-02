from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import db
from seguridad import (
    crear_token,
    hash_password,
    limite_login,
    usuario_actual,
    verificar_password,
)

router = APIRouter(prefix="/api/auth", tags=["Autenticación"])


class LoginInput(BaseModel):
    email: str
    password: str


class CambiarPasswordInput(BaseModel):
    password_actual: str
    password_nuevo: str = Field(..., min_length=6)


@router.post("/login", dependencies=[Depends(limite_login)])
def login(d: LoginInput):
    email_limpio = d.email.strip().lower()
    u = db("SELECT * FROM usuarios WHERE lower(email)=%s", (email_limpio,), "one")
    if not u or not verificar_password(d.password, u["password_hash"]):
        raise HTTPException(401, "Correo o contraseña incorrectos")
    if not u.get("activo", True):
        raise HTTPException(403, "Esta cuenta de usuario se encuentra desactivada. Contacta al administrador.")
    token = crear_token(u)
    return {
        "token": token,
        "id": u["id"],
        "nombre": u["nombre"],
        "email": u["email"],
        "rol": u["rol"],
        "forzar_cambio_password": bool(u.get("forzar_cambio_password", False)),
    }


@router.get("/me")
def perfil_actual(u: dict = Depends(usuario_actual)):
    uid = int(u.get("id") or u["sub"])
    fila = db(
        "SELECT id, nombre, email, rol, activo, forzar_cambio_password FROM usuarios WHERE id=%s",
        (uid,),
        "one",
    )
    if not fila:
        return u
    if not fila.get("activo", True):
        raise HTTPException(403, "Tu cuenta ha sido desactivada")
    return fila


@router.post("/cambiar-password")
def cambiar_password(d: CambiarPasswordInput, u: dict = Depends(usuario_actual)):
    nuevo = d.password_nuevo.strip()
    if len(nuevo) < 6:
        raise HTTPException(422, "La nueva contraseña debe tener al menos 6 caracteres")
    if nuevo == "admin123":
        raise HTTPException(422, "Por seguridad no puedes usar la contraseña predeterminada 'admin123'")

    uid = int(u.get("id") or u["sub"])
    fila = db("SELECT * FROM usuarios WHERE id=%s", (uid,), "one")
    if not fila:
        raise HTTPException(404, "Usuario no encontrado")
    if not verificar_password(d.password_actual, fila["password_hash"]):
        raise HTTPException(401, "La contraseña actual no es correcta")

    nuevo_hash = hash_password(nuevo)
    actualizado = db(
        """UPDATE usuarios
           SET password_hash=%s, forzar_cambio_password=FALSE
           WHERE id=%s
           RETURNING id, nombre, email, rol, activo, forzar_cambio_password""",
        (nuevo_hash, uid),
        "one",
    )
    nuevo_token = crear_token(actualizado)
    return {
        "ok": True,
        "mensaje": "Contraseña actualizada correctamente",
        "token": nuevo_token,
        "usuario": actualizado,
    }
