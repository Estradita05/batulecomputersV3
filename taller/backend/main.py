from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import CORS_ORIGINS, UPLOAD_DIR
from db import abrir_pool, cerrar_pool, db, inicializar_base_datos, pool
from routers import auth, chat, clientes, cotizaciones, ordenes, publico, usuarios
from seguridad import usuario_actual
from services.storage import storage
from services.ws import avisar, manejar_ws, salas


@asynccontextmanager
async def lifespan(app: FastAPI):
    abrir_pool()
    inicializar_base_datos()
    yield
    cerrar_pool()


app = FastAPI(
    title="Taller de Cómputo — Versión 2",
    description="API REST para seguimiento de órdenes de reparación, cotizaciones con IVA, chat en tiempo real y administración de taller.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


@app.middleware("http")
async def deshabilitar_cache_web(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api") and not path.startswith("/uploads"):
        # Evitar respuestas 304 de caché del navegador en HTML/CSS/JS
        request.scope["headers"] = [
            (k, v)
            for k, v in request.scope["headers"]
            if k.lower() not in (b"if-none-match", b"if-modified-since")
        ]
    response = await call_next(request)
    if not path.startswith("/api") and not path.startswith("/uploads"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.websocket("/ws/{folio}")
async def ws_chat(ws: WebSocket, folio: str):
    await manejar_ws(ws, folio)


# Registrar módulos de routers
app.include_router(auth.router)
app.include_router(usuarios.router)
app.include_router(clientes.router)
app.include_router(ordenes.router)
app.include_router(cotizaciones.router)
app.include_router(chat.router)
app.include_router(publico.router)

# Archivos estáticos de imágenes subidas y aplicación web
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/", StaticFiles(directory=str((UPLOAD_DIR.parent / ".." / "web").resolve()), html=True), name="web")
