import io
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageOps

from config import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    IMAGE_MAX_SIZE,
    IMAGE_QUALITY,
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_PUBLIC_URL,
    S3_REGION,
    STORAGE_PROVIDER,
    UPLOAD_DIR,
)

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp"}
MAX_FILE_BYTES = 15 * 1024 * 1024  # 15 MB antes de comprimir


def comprimir_imagen(raw_bytes: bytes) -> bytes:
    """
    Normaliza orientación EXIF, convierte a RGB, redimensiona manteniendo proporción
    hasta IMAGE_MAX_SIZE px por lado y comprime en formato JPEG optimizado.
    """
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise HTTPException(413, "La imagen excede el tamaño máximo permitido (15 MB)")
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img = ImageOps.exif_transpose(img)
    except Exception:
        raise HTTPException(422, "El archivo enviado no es una imagen válida")

    if img.mode in ("RGBA", "LA", "P"):
        fondo = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        fondo.paste(img, mask=img.split()[-1])
        img = fondo
    elif img.mode != "RGB":
        img = img.convert("RGB")

    img.thumbnail((IMAGE_MAX_SIZE, IMAGE_MAX_SIZE), Image.Resampling.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=IMAGE_QUALITY, optimize=True)
    return out.getvalue()


class StorageProvider(ABC):
    @abstractmethod
    def guardar(self, subcarpeta: str, nombre_archivo: str, contenido: bytes, content_type: str = "image/jpeg") -> str:
        """Guarda los bytes y devuelve la URL pública o ruta relativa accesible por HTTP."""

    @abstractmethod
    def eliminar(self, url: str) -> None:
        """Elimina un archivo previamente guardado si existe."""


class LocalStorageProvider(StorageProvider):
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def guardar(self, subcarpeta: str, nombre_archivo: str, contenido: bytes, content_type: str = "image/jpeg") -> str:
        destino_dir = self.base_dir / subcarpeta
        destino_dir.mkdir(parents=True, exist_ok=True)
        ruta = destino_dir / nombre_archivo
        ruta.write_bytes(contenido)
        return f"/uploads/{subcarpeta}/{nombre_archivo}"

    def eliminar(self, url: str) -> None:
        prefijo = "/uploads/"
        if url and url.startswith(prefijo):
            rel = url[len(prefijo):]
            archivo = (self.base_dir / rel).resolve()
            if str(archivo).startswith(str(self.base_dir.resolve())) and archivo.is_file():
                archivo.unlink(missing_ok=True)


class S3StorageProvider(StorageProvider):
    def __init__(self):
        import boto3
        self.bucket = S3_BUCKET
        self.client = boto3.client(
            "s3",
            region_name=S3_REGION,
            endpoint_url=S3_ENDPOINT_URL,
            aws_access_key_id=AWS_ACCESS_KEY_ID or None,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY or None,
        )

    def guardar(self, subcarpeta: str, nombre_archivo: str, contenido: bytes, content_type: str = "image/jpeg") -> str:
        key = f"{subcarpeta}/{nombre_archivo}".lstrip("/")
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=contenido,
            ContentType=content_type,
        )
        if S3_PUBLIC_URL:
            return f"{S3_PUBLIC_URL.rstrip('/')}/{key}"
        if S3_ENDPOINT_URL:
            return f"{S3_ENDPOINT_URL.rstrip('/')}/{self.bucket}/{key}"
        return f"https://{self.bucket}.s3.{S3_REGION}.amazonaws.com/{key}"

    def eliminar(self, url: str) -> None:
        if not url:
            return
        # Extraer key a partir de la URL
        partes = url.split("/")
        if len(partes) >= 2:
            key = "/".join(partes[-2:])
            try:
                self.client.delete_object(Bucket=self.bucket, Key=key)
            except Exception:
                pass


def obtener_storage() -> StorageProvider:
    if STORAGE_PROVIDER == "s3":
        if not S3_BUCKET:
            raise RuntimeError("STORAGE_PROVIDER=s3 requiere configurar S3_BUCKET en .env")
        return S3StorageProvider()
    return LocalStorageProvider(UPLOAD_DIR)


storage = obtener_storage()


async def procesar_y_guardar_imagen(archivo: UploadFile, subcarpeta: str) -> str:
    """Lee un UploadFile, valida que sea imagen, la comprime a JPEG y la guarda en el proveedor."""
    if archivo.content_type and archivo.content_type.lower() not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(422, f"Formato no soportado ({archivo.content_type}). Usa JPG, PNG o WebP.")
    raw = await archivo.read()
    if not raw:
        raise HTTPException(422, "El archivo está vacío")
    optimizada = comprimir_imagen(raw)
    nombre = f"{uuid.uuid4().hex}.jpg"
    return storage.guardar(subcarpeta, nombre, optimizada, "image/jpeg")


def guardar_firma_base64(data_url: str, folio: str) -> str:
    """Decodifica una firma dibujada en canvas (data:image/png;base64,...), la valida y la guarda como imagen."""
    import base64

    if not data_url or not data_url.strip():
        raise HTTPException(422, "La firma de conformidad es obligatoria para entregar el equipo")
    limpio = data_url.strip()
    if "," in limpio:
        limpio = limpio.split(",", 1)[1]
    try:
        raw = base64.b64decode(limpio)
    except Exception:
        raise HTTPException(422, "Formato de firma inválido")
    if len(raw) < 100:
        raise HTTPException(422, "La firma está vacía")
    optimizada = comprimir_imagen(raw)
    nombre = f"firma_{uuid.uuid4().hex}.jpg"
    return storage.guardar(f"firmas/{folio}", nombre, optimizada, "image/jpeg")
