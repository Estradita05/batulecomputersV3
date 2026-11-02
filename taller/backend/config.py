import os
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Cargar .env desde la raíz de taller/ o desde backend/
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")
load_dotenv(BASE_DIR / ".env")

DB_URL = os.getenv("DATABASE_URL", "postgresql://taller:taller@localhost:5432/taller")
SECRET = os.getenv("JWT_SECRET", "cambia-esto-en-produccion")
EQUIPMENT_SECRET_KEY = os.getenv("EQUIPMENT_SECRET_KEY", SECRET + "-equipo-key")
TIMEZONE = os.getenv("TIMEZONE", "America/Mexico_City")
TZ_INFO = ZoneInfo(TIMEZONE)

CORS_ORIGINS_RAW = os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
CORS_ORIGINS = [o.strip() for o in CORS_ORIGINS_RAW.split(",") if o.strip()]

DEFAULT_IVA = float(os.getenv("DEFAULT_IVA", "16"))

# Almacenamiento de fotos y archivos (local | s3)
STORAGE_PROVIDER = os.getenv("STORAGE_PROVIDER", "local").lower()
_upload_env = os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads"))
UPLOAD_DIR = Path(_upload_env) if Path(_upload_env).is_absolute() else (BASE_DIR / _upload_env)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

IMAGE_MAX_SIZE = int(os.getenv("IMAGE_MAX_SIZE", "1600"))
IMAGE_QUALITY = int(os.getenv("IMAGE_QUALITY", "80"))

# S3 / Compatible (Cloudflare R2, MinIO, AWS S3)
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "") or None
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
S3_PUBLIC_URL = os.getenv("S3_PUBLIC_URL", "")

ESTATUS = [
    "Recibido",
    "En diagnóstico",
    "Cotización enviada",
    "Esperando aprobación",
    "Esperando refacción",
    "En reparación",
    "En pruebas",
    "Listo para entrega",
    "Entregado",
    "No reparable / Cancelado",
]

ETAPAS_FOTO = ["recepcion", "proceso", "entrega"]
ROLES_VALIDOS = ("admin", "recepcion", "tecnico")
