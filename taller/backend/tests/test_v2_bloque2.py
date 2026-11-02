import pytest
from fastapi.testclient import TestClient

import main
from db import db
from seguridad import hash_password, limite_login, limite_publico


@pytest.fixture(autouse=True)
def limpiar_estado():
    main.app.dependency_overrides.clear()
    limite_login.reiniciar()
    limite_publico.reiniciar()
    yield
    main.app.dependency_overrides.clear()
    limite_login.reiniciar()
    limite_publico.reiniciar()


def test_uso_diario_bloque2():
    with TestClient(main.app) as client:
        # Asegurar admin activo sin bloqueo de cambio de contraseña durante la prueba
        db(
            "UPDATE usuarios SET password_hash=%s, forzar_cambio_password=FALSE WHERE email='admin@taller.local'",
            (hash_password("AdminSegura2026"),),
            fetch=None,
        )
        token_admin = client.post(
            "/api/auth/login", json={"email": "admin@taller.local", "password": "AdminSegura2026"}
        ).json()["token"]
        headers = {"Authorization": f"Bearer {token_admin}"}

        # Crear técnico para pruebas de asignación
        db(
            """UPDATE ordenes SET tecnico_id=NULL WHERE tecnico_id IN (SELECT id FROM usuarios WHERE email='tec2@taller.local');
               UPDATE historial_estatus SET usuario_id=NULL WHERE usuario_id IN (SELECT id FROM usuarios WHERE email='tec2@taller.local');
               UPDATE bitacora_ordenes SET usuario_id=NULL WHERE usuario_id IN (SELECT id FROM usuarios WHERE email='tec2@taller.local');
               DELETE FROM usuarios WHERE email='tec2@taller.local';""",
            fetch=None,
        )
        r_tec = client.post(
            "/api/usuarios",
            headers=headers,
            json={
                "nombre": "Roberto Técnico",
                "email": "tec2@taller.local",
                "password": "tecnico123",
                "rol": "tecnico",
                "forzar_cambio_password": False,
            },
        )
        assert r_tec.status_code == 200
        tec_id = r_tec.json()["id"]

        # 1. Crear primera orden con número de serie y fecha estimada vencida (retrasada)
        r_o1 = client.post(
            "/api/ordenes",
            headers=headers,
            json={
                "cliente_nombre": "María Hernández",
                "cliente_telefono": "55-4433-2211",
                "tipo": "Laptop",
                "marca": "HP",
                "modelo": "Pavilion 14",
                "num_serie": "SN-HP-REINCIDENTE-99",
                "falla": "Bisagra rota",
                "fecha_estimada": "2020-01-15",
            },
        )
        assert r_o1.status_code == 200
        folio1 = r_o1.json()["folio"]

        # 2. Crear segunda orden del mismo cliente y mismo número de serie para probar historial previo
        r_o2 = client.post(
            "/api/ordenes",
            headers=headers,
            json={
                "cliente_nombre": "María Hernández",
                "cliente_telefono": "55-4433-2211",
                "tipo": "Laptop",
                "marca": "HP",
                "modelo": "Pavilion 14",
                "num_serie": "SN-HP-REINCIDENTE-99",
                "falla": "Mantenimiento general y cambio de batería",
                "fecha_estimada": "2030-12-31",
            },
        )
        assert r_o2.status_code == 200
        folio2 = r_o2.json()["folio"]

        # 3. Verificar historial previo por cliente y por número de serie al abrir folio2
        det2 = client.get(f"/api/ordenes/{folio2}", headers=headers).json()
        prev_cli_folios = [x["folio"] for x in det2["historial_previo"]["cliente"]]
        prev_sn_folios = [x["folio"] for x in det2["historial_previo"]["num_serie"]]
        assert folio1 in prev_cli_folios
        assert folio1 in prev_sn_folios

        # 4. Asignar y reasignar técnico a folio1
        r_asig = client.patch(
            f"/api/ordenes/{folio1}/tecnico",
            headers=headers,
            json={"tecnico_id": tec_id},
        )
        assert r_asig.status_code == 200
        assert r_asig.json()["tecnico_nombre"] == "Roberto Técnico"

        # 5. Búsqueda global (por serie, nombre, teléfono y folio) y filtros (técnico, retrasados)
        busq_sn = client.get("/api/ordenes?q=SN-HP-REINCIDENTE-99", headers=headers).json()
        folios_sn = [x["folio"] for x in busq_sn]
        assert folio1 in folios_sn and folio2 in folios_sn

        busq_tec = client.get(f"/api/ordenes?tecnico_id={tec_id}", headers=headers).json()
        assert any(x["folio"] == folio1 and x["tecnico_nombre"] == "Roberto Técnico" for x in busq_tec)

        busq_ret = client.get("/api/ordenes?retrasados=true", headers=headers).json()
        folios_ret = [x["folio"] for x in busq_ret]
        assert folio1 in folios_ret
        assert folio2 not in folios_ret

        # 6. Indicador de mensajes sin leer por orden y marcado automático al abrir
        r_msg_cli = client.post(
            f"/api/public/seguimiento/{folio1}/mensajes?telefono=5544332211",
            json={"texto": "Hola, ¿cómo va mi laptop?"},
        )
        assert r_msg_cli.status_code == 200

        lista_antes = client.get(f"/api/ordenes?q={folio1}", headers=headers).json()
        fila_f1 = next(x for x in lista_antes if x["folio"] == folio1)
        assert int(fila_f1["mensajes_no_leidos"]) >= 1

        # Al abrir el detalle de la orden como técnico/admin, se marcan como leídos
        client.get(f"/api/ordenes/{folio1}", headers=headers)
        lista_despues = client.get(f"/api/ordenes?q={folio1}", headers=headers).json()
        fila_f1_desp = next(x for x in lista_despues if x["folio"] == folio1)
        assert int(fila_f1_desp["mensajes_no_leidos"]) == 0

        # 7. Respuestas rápidas en el chat (CRUD admin)
        r_rr_crear = client.post(
            "/api/respuestas-rapidas",
            headers=headers,
            json={"titulo": "Prueba rápida", "texto": "Equipo en pruebas finales de estrés.", "activo": True, "orden": 10},
        )
        assert r_rr_crear.status_code == 200
        rr_id = r_rr_crear.json()["id"]

        rrs = client.get("/api/respuestas-rapidas", headers=headers).json()
        assert any(r["id"] == rr_id for r in rrs)

        r_rr_del = client.delete(f"/api/respuestas-rapidas/{rr_id}", headers=headers)
        assert r_rr_del.status_code == 200

        # 8. Dashboard de resumen (equipos por estatus, retrasados, listos sin recoger, ingresos del mes)
        client.patch(f"/api/ordenes/{folio2}/estatus", headers=headers, json={"estatus": "Listo para entrega"})
        dash = client.get("/api/dashboard", headers=headers).json()
        assert dash["retrasados"] >= 1
        assert dash["listos_sin_recoger"] >= 1
        assert isinstance(dash["por_estatus"], list)
        assert dash["ingresos_mes_fmt"].startswith("$")

        # 9. Módulo de Clientes: registro con número de cliente, bloqueo de teléfonos repetidos y orden con cliente_id
        db(
            """DELETE FROM ordenes WHERE equipo_id IN (SELECT id FROM equipos WHERE cliente_id IN (SELECT id FROM clientes WHERE telefono='5599887766'));
               DELETE FROM equipos WHERE cliente_id IN (SELECT id FROM clientes WHERE telefono='5599887766');
               DELETE FROM clientes WHERE telefono='5599887766';""",
            fetch=None,
        )
        r_cli = client.post(
            "/api/clientes",
            headers=headers,
            json={"nombre": "Pedro Ramírez", "telefono": "55-9988-7766", "correo": "pedro@taller.mx"},
        )
        assert r_cli.status_code == 200
        cli_data = r_cli.json()
        assert cli_data["numero_cliente"].startswith("CLI-")
        assert cli_data["telefono"] == "5599887766"

        # Intentar registrar otro cliente con el mismo teléfono (aunque tenga formato distinto) debe dar 409
        r_cli_dup = client.post(
            "/api/clientes",
            headers=headers,
            json={"nombre": "Pedro Duplicado", "telefono": "5599887766"},
        )
        assert r_cli_dup.status_code == 409

        # Registrar equipo usando el cliente_id ya registrado
        r_o_cli = client.post(
            "/api/ordenes",
            headers=headers,
            json={
                "cliente_id": cli_data["id"],
                "tipo": "All in One",
                "marca": "Lenovo",
                "modelo": "IdeaCentre",
                "falla": "No da video",
            },
        )
        assert r_o_cli.status_code == 200
        assert r_o_cli.json()["numero_cliente"] == cli_data["numero_cliente"]

        # Intentar registrar equipo con un cliente_id inexistente debe dar 404
        r_o_inex = client.post(
            "/api/ordenes",
            headers=headers,
            json={"cliente_id": 999999, "tipo": "CPU", "falla": "No prende"},
        )
        assert r_o_inex.status_code == 404

        # Restaurar contraseña inicial de admin para uso en navegador
        db(
            "UPDATE usuarios SET password_hash=%s, forzar_cambio_password=TRUE WHERE email='admin@taller.local'",
            (hash_password("admin123"),),
            fetch=None,
        )
