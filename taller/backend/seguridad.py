import base64
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import EQUIPMENT_SECRET_KEY, SECRET, TZ_INFO

bearer = HTTPBearer(auto_error=False)


# ---------- Utilidades de formato y teléfono (México) ----------
def normalizar_telefono(t: str) -> str:
    d = re.sub(r"\D", "", t or "")
    return d[-10:]


def formatear_mxn(monto: float | int | None) -> str:
    if monto is None:
        return "$0.00"
    return f"${float(monto):,.2f}"


def ahora_mx() -> datetime:
    return datetime.now(TZ_INFO)


def a_zona_mx(dt: datetime | str | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_INFO).isoformat()


# ---------- Cifrado simétrico autenticado para contraseña/patrón de equipo ----------
def _derivar_flujo(clave: bytes, nonce: bytes, longitud: int) -> bytes:
    bloques = []
    contador = 0
    while sum(len(b) for b in bloques) < longitud:
        bloques.append(
             hmac.new(clave, nonce + contador.to_bytes(4, "big"), hashlib.sha256).digest()
        )
        contador += 1
    return b"".join(bloques)[:longitud]


def cifrar_clave_equipo(texto: str | None) -> str | None:
    if not texto or not texto.strip():
        return None
    limpio = texto.strip()
    if limpio.startswith("enc:v1:"):
        return limpio
    raw = limpio.encode("utf-8")
    master = hashlib.sha256(EQUIPMENT_SECRET_KEY.encode("utf-8")).digest()
    nonce = secrets.token_bytes(16)
    flujo = _derivar_flujo(master, nonce, len(raw))
    cifrado = bytes(a ^ b for a, b in zip(raw, flujo))
    mac = hmac.new(master, nonce + cifrado, hashlib.sha256).digest()[:16]
    token = base64.urlsafe_b64encode(nonce + mac + cifrado).decode("ascii")
    return f"enc:v1:{token}"


def descifrar_clave_equipo(valor: str | None) -> str | None:
    if not valor:
        return None
    if not valor.startswith("enc:v1:"):
        # Compatibilidad con registros en texto plano antes de migrar
        return valor
    try:
        datos = base64.urlsafe_b64decode(valor[len("enc:v1:"):].encode("ascii"))
        nonce, mac_recibido, cifrado = datos[:16], datos[16:32], datos[32:]
        master = hashlib.sha256(EQUIPMENT_SECRET_KEY.encode("utf-8")).digest()
        mac_esperado = hmac.new(master, nonce + cifrado, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(mac_recibido, mac_esperado):
            return "[Error: clave corrupta]"
        flujo = _derivar_flujo(master, nonce, len(cifrado))
        plano = bytes(a ^ b for a, b in zip(cifrado, flujo))
        return plano.decode("utf-8")
    except Exception:
        return "[No disponible]"


# ---------- Contraseñas de usuarios y JWT ----------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verificar_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def crear_token(u: dict) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=12)
    payload = {
        "sub": str(u["id"]),
        "id": u["id"],
        "nombre": u["nombre"],
        "email": u.get("email", ""),
        "rol": u["rol"],
        "forzar_cambio_password": bool(u.get("forzar_cambio_password", False)),
        "exp": exp,
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


def usuario_actual(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if not cred or not cred.credentials:
        raise HTTPException(401, "Debes iniciar sesión para acceder a este recurso")
    try:
        datos = jwt.decode(cred.credentials, SECRET, algorithms=["HS256"])
        return datos
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Tu sesión ha expirado, inicia sesión nuevamente")
    except jwt.PyJWTError:
        raise HTTPException(401, "Sesión inválida")


def requerir_rol(*roles_permitidos: str):
    """
    Dependencia reutilizable para validar roles en la API:
    - admin: tiene acceso a todos los endpoints.
    - recepcion / tecnico: según se indique en roles_permitidos.
    También verifica que el usuario no tenga pendiente el cambio obligatorio de contraseña.
    """
    def _validador(u: dict = Depends(usuario_actual)) -> dict:
        if u.get("forzar_cambio_password"):
            raise HTTPException(
                403,
                "Por seguridad debes cambiar tu contraseña inicial antes de continuar.",
            )
        rol = u.get("rol")
        if rol == "admin" or rol in roles_permitidos:
            return u
        raise HTTPException(
            403,
            f"No tienes permisos para realizar esta acción (se requiere rol: {', '.join(roles_permitidos)})",
        )

    return _validador


# ---------- Rate Limiting por IP (ventana deslizante en memoria) ----------
class LimitadorTasa:
    def __init__(self, max_peticiones: int, ventana_segundos: int = 60, mensaje: str = ""):
        self.max_peticiones = max_peticiones
        self.ventana_segundos = ventana_segundos
        self.mensaje = mensaje or (
            f"Demasiadas solicitudes. Límite máximo: {max_peticiones} por minuto. Intenta más tarde."
        )
        self.historial: dict[str, deque[float]] = defaultdict(deque)

    def reiniciar(self):
        self.historial.clear()

    def obtener_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client and request.client.host:
            return request.client.host
        return "127.0.0.1"

    def __call__(self, request: Request):
        ip = self.obtener_ip(request)
        ahora = time.monotonic()
        cola = self.historial[ip]
        while cola and (ahora - cola[0]) > self.ventana_segundos:
            cola.popleft()
        if len(cola) >= self.max_peticiones:
            raise HTTPException(429, self.mensaje)
        cola.append(ahora)


limite_login = LimitadorTasa(
    max_peticiones=5,
    ventana_segundos=60,
    mensaje="Demasiados intentos de inicio de sesión (máximo 5 por minuto). Espera un minuto e intenta de nuevo.",
)

limite_publico = LimitadorTasa(
    max_peticiones=10,
    ventana_segundos=60,
    mensaje="Has superado el límite de 10 consultas por minuto. Por favor espera un momento.",
)
