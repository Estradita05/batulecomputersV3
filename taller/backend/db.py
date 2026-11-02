import sys
from pathlib import Path
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from config import BASE_DIR, DB_URL, TIMEZONE
from seguridad import cifrar_clave_equipo, hash_password

def crear_pool() -> ConnectionPool:
    return ConnectionPool(
        DB_URL,
        kwargs={"row_factory": dict_row, "options": f"-c timezone={TIMEZONE}"},
        open=False,
    )


pool = crear_pool()


def abrir_pool():
    global pool
    if getattr(pool, "closed", False):
        pool = crear_pool()
    if not getattr(pool, "_opened", False):
        pool.open()


def cerrar_pool():
    if getattr(pool, "_opened", False) and not getattr(pool, "closed", False):
        pool.close()


def obtener_conexion():
    abrir_pool()
    return pool.connection()


def _raw_db(sql: str, params: tuple | list = (), fetch: str | None = "all"):
    abrir_pool()
    with pool.connection() as c:
        cur = c.execute(sql, params)
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return None


def db(sql: str, params: tuple | list = (), fetch: str | None = "all"):
    main_mod = sys.modules.get("main")
    if main_mod is not None:
        override = getattr(main_mod, "db", None)
        if override is not None and override is not db and override is not _raw_db:
            return override(sql, params, fetch)
    return _raw_db(sql, params, fetch)


def inicializar_base_datos():
    """
    Aplica el esquema base y la migración 002_v2.sql de forma idempotente, asegura el
    usuario admin inicial con forzar_cambio_password=TRUE y cifra contraseñas de equipos
    que hayan quedado en texto plano desde la Fase 1.
    """
    schema_sql = (BASE_DIR / "schema.sql").read_text(encoding="utf-8")
    db(schema_sql, fetch=None)
    migracion_sql = (BASE_DIR / "migrations" / "002_v2.sql").read_text(encoding="utf-8")
    db(migracion_sql, fetch=None)

    # Crear admin inicial si no hay usuarios
    if not db("SELECT 1 FROM usuarios LIMIT 1", fetch="one"):
        h = hash_password("admin123")
        db(
            """INSERT INTO usuarios(nombre, email, password_hash, rol, activo, forzar_cambio_password)
               VALUES('Administrador', 'admin@taller.local', %s, 'admin', TRUE, TRUE)""",
            (h,),
            fetch=None,
        )
        print("Usuario inicial: admin@taller.local / admin123  (Se solicitará cambio en el primer inicio de sesión)")
    else:
        # Si el admin inicial sigue teniendo la contraseña por defecto 'admin123', marcar forzar_cambio_password=TRUE
        admin_row = db("SELECT id, password_hash FROM usuarios WHERE email='admin@taller.local'", fetch="one")
        if admin_row:
            from seguridad import verificar_password
            if verificar_password("admin123", admin_row["password_hash"]):
                db("UPDATE usuarios SET forzar_cambio_password=TRUE WHERE id=%s", (admin_row["id"],), fetch=None)

    # Migrar contraseñas de equipos en texto plano a cifrado simétrico
    pendientes = db(
        """SELECT id, password_equipo FROM ordenes
           WHERE password_equipo IS NOT NULL AND password_equipo <> ''
             AND (password_equipo_cifrado IS NULL OR password_equipo_cifrado = '')"""
    )
    for fila in pendientes or []:
        cifrada = cifrar_clave_equipo(fila["password_equipo"])
        db(
            "UPDATE ordenes SET password_equipo_cifrado=%s, password_equipo=NULL WHERE id=%s",
            (cifrada, fila["id"]),
            fetch=None,
        )

    # Recalcular subtotal/iva/total de cotizaciones existentes de Fase 1 que tengan total=0
    cots_viejas = db("SELECT id, iva_porcentaje FROM cotizaciones WHERE total = 0")
    for cot in cots_viejas or []:
        items = db("SELECT cantidad, precio FROM cotizacion_items WHERE cotizacion_id=%s", (cot["id"],))
        if items:
            sub = round(sum(float(i["cantidad"]) * float(i["precio"]) for i in items), 2)
            iva_pct = float(cot["iva_porcentaje"] or 16.0)
            iva_m = round(sub * (iva_pct / 100.0), 2)
            tot = round(sub + iva_m, 2)
            db(
                "UPDATE cotizaciones SET subtotal=%s, iva_monto=%s, total=%s WHERE id=%s",
                (sub, iva_m, tot, cot["id"]),
                fetch=None,
            )

    # Sembrar respuestas rápidas iniciales si la tabla está vacía
    if not db("SELECT 1 FROM respuestas_rapidas LIMIT 1", fetch="one"):
        plantillas = [
            ("En diagnóstico", "Hola, tu equipo ya se encuentra en mesa de diagnóstico. Te avisaremos por este medio en cuanto tengamos el reporte.", 1),
            ("Cotización lista", "Hemos subido la cotización de reparación de tu equipo. Puedes revisarla y aprobarla directamente desde esta página.", 2),
            ("Refacción en camino", "Tu reparación fue aprobada y estamos esperando la llegada de la refacción para instalarla.", 3),
            ("Listo para recoger", "¡Tu equipo ya está listo para entrega! Puedes pasar por él en nuestro horario de atención presentando tu folio.", 4),
        ]
        for titulo, texto, orden in plantillas:
            db(
                "INSERT INTO respuestas_rapidas(titulo, texto, activo, orden) VALUES(%s, %s, TRUE, %s)",
                (titulo, texto, orden),
                fetch=None,
            )
