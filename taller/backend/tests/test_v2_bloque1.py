import pytest
from fastapi.testclient import TestClient

import main
from db import db
from seguridad import limite_login, limite_publico


@pytest.fixture(autouse=True)
def limpiar_rate_limiters():
    main.app.dependency_overrides.clear()
    limite_login.reiniciar()
    limite_publico.reiniciar()
    yield
    main.app.dependency_overrides.clear()
    limite_login.reiniciar()
    limite_publico.reiniciar()


def test_flujo_completo_bloque1():
    with TestClient(main.app) as client:
        # Asegurar estado limpio de prueba en BD para usuarios/órdenes de prueba
        db(
            """UPDATE ordenes SET tecnico_id=NULL WHERE tecnico_id IN (SELECT id FROM usuarios WHERE email IN ('recep@taller.local', 'tec@taller.local'));
               UPDATE historial_estatus SET usuario_id=NULL WHERE usuario_id IN (SELECT id FROM usuarios WHERE email IN ('recep@taller.local', 'tec@taller.local'));
               UPDATE bitacora_ordenes SET usuario_id=NULL WHERE usuario_id IN (SELECT id FROM usuarios WHERE email IN ('recep@taller.local', 'tec@taller.local'));
               DELETE FROM usuarios WHERE email IN ('recep@taller.local', 'tec@taller.local');""",
            fetch=None,
        )
        from seguridad import hash_password
        db(
            "UPDATE usuarios SET password_hash=%s, forzar_cambio_password=TRUE WHERE email='admin@taller.local'",
            (hash_password("admin123"),),
            fetch=None,
        )

        # 1. Login inicial del admin debe indicar forzar_cambio_password = True
        r_login = client.post("/api/auth/login", json={"email": "admin@taller.local", "password": "admin123"})
        assert r_login.status_code == 200
        datos_login = r_login.json()
        assert datos_login["forzar_cambio_password"] is True
        token_inicial = datos_login["token"]

        # Intentar usar un endpoint protegido sin cambiar la contraseña inicial debe dar 403
        r_bloq = client.get("/api/ordenes", headers={"Authorization": f"Bearer {token_inicial}"})
        assert r_bloq.status_code == 403

        # Cambiar contraseña inicial del admin
        r_cambio = client.post(
            "/api/auth/cambiar-password",
            headers={"Authorization": f"Bearer {token_inicial}"},
            json={"password_actual": "admin123", "password_nuevo": "AdminSegura2026"},
        )
        assert r_cambio.status_code == 200
        token_admin = r_cambio.json()["token"]
        assert r_cambio.json()["usuario"]["forzar_cambio_password"] is False

        # 2. Administración de usuarios (crear recepción y técnico)
        r_u_rec = client.post(
            "/api/usuarios",
            headers={"Authorization": f"Bearer {token_admin}"},
            json={
                "nombre": "Laura Recepción",
                "email": "recep@taller.local",
                "password": "recep123",
                "rol": "recepcion",
                "forzar_cambio_password": False,
            },
        )
        assert r_u_rec.status_code == 200

        r_u_tec = client.post(
            "/api/usuarios",
            headers={"Authorization": f"Bearer {token_admin}"},
            json={
                "nombre": "Carlos Técnico",
                "email": "tec@taller.local",
                "password": "tecnico123",
                "rol": "tecnico",
                "forzar_cambio_password": False,
            },
        )
        assert r_u_tec.status_code == 200

        token_rec = client.post("/api/auth/login", json={"email": "recep@taller.local", "password": "recep123"}).json()["token"]
        token_tec = client.post("/api/auth/login", json={"email": "tec@taller.local", "password": "tecnico123"}).json()["token"]

        # Verificar permisos de rol: recepción y técnico NO pueden administrar usuarios
        assert client.get("/api/usuarios", headers={"Authorization": f"Bearer {token_rec}"}).status_code == 403
        assert client.get("/api/usuarios", headers={"Authorization": f"Bearer {token_tec}"}).status_code == 403

        # 3. Recepción crea una orden con contraseña de equipo
        r_crear = client.post(
            "/api/ordenes",
            headers={"Authorization": f"Bearer {token_rec}"},
            json={
                "cliente_nombre": "Juan Pérez",
                "cliente_telefono": "55-1122-3344",
                "cliente_correo": "juan@ejemplo.mx",
                "tipo": "Laptop",
                "marca": "Dell",
                "modelo": "Inspiron 15",
                "num_serie": "SN-DELL-001",
                "falla": "No enciende al conectar cargador",
                "accesorios": "Cargador original 65W",
                "password_equipo": "PinSecreto9876",
                "fecha_estimada": "2026-11-10",
            },
        )
        assert r_crear.status_code == 200
        folio = r_crear.json()["folio"]
        assert folio.startswith("SRV-")

        # Técnico NO puede crear órdenes nuevas (403)
        r_crear_tec = client.post(
            "/api/ordenes",
            headers={"Authorization": f"Bearer {token_tec}"},
            json={
                "cliente_nombre": "Otro",
                "cliente_telefono": "5511223344",
                "tipo": "CPU",
                "falla": "Lento",
            },
        )
        assert r_crear_tec.status_code == 403

        # 4. Verificar cifrado simétrico de la contraseña del equipo en base de datos
        fila_bd = db("SELECT password_equipo, password_equipo_cifrado FROM ordenes WHERE folio=%s", (folio,), "one")
        assert fila_bd["password_equipo"] is None
        assert fila_bd["password_equipo_cifrado"].startswith("enc:v1:")
        assert "PinSecreto9876" not in fila_bd["password_equipo_cifrado"]

        # Al consultar el detalle, la contraseña no viene descifrada por defecto
        det_tec = client.get(f"/api/ordenes/{folio}", headers={"Authorization": f"Bearer {token_tec}"}).json()
        assert det_tec["password_equipo"] == "••••••••"
        assert det_tec["tiene_password_equipo"] is True

        # Recepción NO puede descifrar la contraseña del equipo (403)
        assert client.get(f"/api/ordenes/{folio}/password-equipo", headers={"Authorization": f"Bearer {token_rec}"}).status_code == 403

        # Técnico y Admin SÍ pueden descifrarla bajo demanda
        r_clave = client.get(f"/api/ordenes/{folio}/password-equipo", headers={"Authorization": f"Bearer {token_tec}"})
        assert r_clave.status_code == 200
        assert r_clave.json()["password_equipo"] == "PinSecreto9876"

        # 5. Edición de orden y registro en bitácora (por recepción)
        r_edit = client.put(
            f"/api/ordenes/{folio}",
            headers={"Authorization": f"Bearer {token_rec}"},
            json={
                "modelo": "Inspiron 15 3520",
                "accesorios": "Cargador original 65W y maletín negro",
            },
        )
        assert r_edit.status_code == 200
        assert r_edit.json()["cambios"] == 2

        det_post_edit = client.get(f"/api/ordenes/{folio}", headers={"Authorization": f"Bearer {token_admin}"}).json()
        assert det_post_edit["modelo"] == "Inspiron 15 3520"
        campos_bitacora = [b["campo"] for b in det_post_edit["bitacora"]]
        assert "Modelo" in campos_bitacora
        assert "Accesorios" in campos_bitacora

        # 6. Cotización con conceptos (refacción y mano de obra), IVA y versionado
        # Recepción NO puede cotizar (403)
        assert client.post(
            f"/api/ordenes/{folio}/cotizacion",
            headers={"Authorization": f"Bearer {token_rec}"},
            json={"items": [{"descripcion": "Diagnóstico", "tipo": "mano_obra", "cantidad": 1, "precio": 300}], "iva_porcentaje": 16},
        ).status_code == 403

        # Técnico crea cotización v1
        r_cot1 = client.post(
            f"/api/ordenes/{folio}/cotizacion",
            headers={"Authorization": f"Bearer {token_tec}"},
            json={
                "items": [
                    {"descripcion": "Circuito de carga DC-In", "tipo": "refaccion", "cantidad": 1, "precio": 450.00},
                    {"descripcion": "Reemplazo y microsoldadura", "tipo": "mano_obra", "cantidad": 1, "precio": 550.00},
                ],
                "iva_porcentaje": 16.0,
            },
        )
        assert r_cot1.status_code == 200
        d_cot1 = r_cot1.json()
        assert d_cot1["version"] == 1
        assert d_cot1["subtotal"] == 1000.00
        assert d_cot1["iva_monto"] == 160.00
        assert d_cot1["total"] == 1160.00
        assert d_cot1["total_fmt"] == "$1,160.00"

        # Técnico edita la cotización pendiente -> sube a v2
        r_cot2 = client.put(
            f"/api/ordenes/{folio}/cotizacion",
            headers={"Authorization": f"Bearer {token_tec}"},
            json={
                "items": [
                    {"descripcion": "Circuito de carga DC-In", "tipo": "refaccion", "cantidad": 1, "precio": 450.00},
                    {"descripcion": "Reemplazo y limpieza preventiva", "tipo": "mano_obra", "cantidad": 1, "precio": 650.00},
                ],
                "iva_porcentaje": 16.0,
            },
        )
        assert r_cot2.status_code == 200
        assert r_cot2.json()["version"] == 2
        assert r_cot2.json()["total"] == 1276.00

        # 7. Consulta pública del cliente: validar teléfono incorrecto (403) y datos privados ocultos
        r_pub_mal = client.get(f"/api/public/seguimiento/{folio}?telefono=5599999999")
        assert r_pub_mal.status_code == 403

        r_pub_ok = client.get(f"/api/public/seguimiento/{folio}?telefono=5511223344")
        assert r_pub_ok.status_code == 200
        pub_data = r_pub_ok.json()
        assert "password_equipo" not in pub_data
        assert "telefono" not in pub_data
        assert "correo" not in pub_data
        assert pub_data["cotizacion"]["version"] == 2
        assert pub_data["cotizacion"]["total"] == 1276.00

        # Cliente aprueba la cotización -> guarda fecha/hora e IP
        r_aprob = client.post(
            f"/api/public/seguimiento/{folio}/cotizacion?telefono=5511223344",
            json={"aprobar": True},
        )
        assert r_aprob.status_code == 200
        det_aprob = client.get(f"/api/ordenes/{folio}", headers={"Authorization": f"Bearer {token_admin}"}).json()
        assert det_aprob["estatus"] == "En reparación"
        assert det_aprob["cotizacion"]["estatus"] == "aprobada"
        assert det_aprob["cotizacion"]["respondida"] is not None
        assert det_aprob["cotizacion"]["ip_respuesta"] is not None

        # Restaurar admin@taller.local a admin123 con cambio forzado para pruebas manuales en navegador
        db(
            "UPDATE usuarios SET password_hash=%s, forzar_cambio_password=TRUE WHERE email='admin@taller.local'",
            (hash_password("admin123"),),
            fetch=None,
        )


def test_rate_limiting_login_y_publico():
    with TestClient(main.app) as client:
        # Máximo 5 intentos de login por minuto por IP
        for _ in range(5):
            r = client.post("/api/auth/login", json={"email": "nadie@taller.local", "password": "mal"})
            assert r.status_code == 401
        r_6 = client.post("/api/auth/login", json={"email": "nadie@taller.local", "password": "mal"})
        assert r_6.status_code == 429

        # Máximo 10 consultas públicas por minuto por IP
        for _ in range(10):
            rp = client.get("/api/public/seguimiento/SRV-2026-00000?telefono=5500000000")
            assert rp.status_code in (403, 404)
        rp_11 = client.get("/api/public/seguimiento/SRV-2026-00000?telefono=5500000000")
        assert rp_11.status_code == 429
