# Taller de cómputo — Versión 2

## Arrancar con Docker (Todo en contenedores)
Desde la raíz o desde la carpeta `taller/`:
```bash
docker compose up -d --build
```
Esto levantará:
- **Base de datos PostgreSQL 16** (`taller-db`) en el puerto `5432`
- **API FastAPI + Web** (`taller-app`) en el puerto `8000`

### Accesos
- **Panel Técnico / Admin**: http://localhost:8000/tecnico.html (`admin@taller.local` / `admin123`, solicita cambio en el primer inicio)
- **Portal Cliente**: http://localhost:8000/ (consulta con folio + teléfono)
- **Documentación API (Swagger)**: http://localhost:8000/docs

### Comandos útiles de Docker
- Ver logs en vivo: `docker compose logs -f app`
- Detener contenedores: `docker compose down`
- Reiniciar tras cambios: `docker compose up -d --build`

---

## Arrancar en modo desarrollo local (sin contenedor para la app)
1. Base de datos: `docker compose up -d db`
2. API + web:
   ```bash
   cd backend
   python -m venv venv && venv\Scripts\activate
   pip install -r requirements.txt
   uvicorn main:app --reload
   ```

Antes de producción: define `JWT_SECRET`, `EQUIPMENT_SECRET_KEY`, usa HTTPS y cambia la contraseña del admin.
