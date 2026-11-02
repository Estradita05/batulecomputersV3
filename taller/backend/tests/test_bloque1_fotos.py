import io
from PIL import Image
from fastapi.testclient import TestClient

import main
from services.storage import comprimir_imagen, LocalStorageProvider


def crear_imagen_prueba(ancho=2000, alto=1500, color=(30, 58, 138), formato="PNG") -> bytes:
    img = Image.new("RGB", (ancho, alto), color=color)
    buf = io.BytesIO()
    img.save(buf, format=formato)
    return buf.getvalue()


def test_compresion_y_redimensionado():
    raw = crear_imagen_prueba(2400, 1800, formato="PNG")
    comprimida = comprimir_imagen(raw)
    assert len(comprimida) > 0
    img_out = Image.open(io.BytesIO(comprimida))
    assert img_out.format == "JPEG"
    assert max(img_out.size) <= 1600


def test_almacenamiento_local(tmp_path):
    prov = LocalStorageProvider(tmp_path)
    raw = comprimir_imagen(crear_imagen_prueba(400, 300))
    url = prov.guardar("ordenes/SRV-2026-00001", "evidencia1.jpg", raw)
    assert url == "/uploads/ordenes/SRV-2026-00001/evidencia1.jpg"
    archivo = tmp_path / "ordenes" / "SRV-2026-00001" / "evidencia1.jpg"
    assert archivo.exists()
    prov.eliminar(url)
    assert not archivo.exists()


def test_endpoints_fotos_y_chat_con_imagen(monkeypatch, tmp_path):
    # Simular almacenamiento local en tmp_path y base de datos en memoria
    prov = LocalStorageProvider(tmp_path)
    monkeypatch.setattr(main, "storage", prov)
    monkeypatch.setattr("services.storage.storage", prov)

    fotos_mem = []
    mensajes_mem = []

    def fake_db(sql, params=(), fetch="all"):
        s = " ".join(sql.split()).upper()
        if "SELECT ID FROM ORDENES WHERE FOLIO=" in s:
            return {"id": 1} if params[0] == "SRV-2026-00001" else None
        if "SELECT C.TELEFONO FROM ORDENES" in s:
            return {"telefono": "5512345678"} if params[0] == "SRV-2026-00001" else None
        if "INSERT INTO FOTOS_ORDEN" in s:
            nueva = {
                "id": len(fotos_mem) + 1,
                "etapa": params[1],
                "archivo_url": params[2],
                "descripcion": params[3],
                "fecha": "2026-10-31T12:00:00Z",
            }
            fotos_mem.append(nueva)
            return nueva
        if "DELETE FROM FOTOS_ORDEN" in s:
            fid = params[0]
            for f in list(fotos_mem):
                if f["id"] == fid:
                    fotos_mem.remove(f)
                    return {"archivo_url": f["archivo_url"]}
            return None
        if "INSERT INTO MENSAJES" in s:
            emisor = "tecnico" if "'TECNICO'" in s else "cliente"
            mensajes_mem.append({"id": len(mensajes_mem) + 1, "emisor": emisor, "texto": params[1], "archivo_url": params[2]})
            return None
        return []

    monkeypatch.setattr(main, "db", fake_db)
    app_test = main.app
    app_test.dependency_overrides[main.usuario_actual] = lambda: {"sub": "1", "id": 1, "nombre": "Admin", "rol": "admin"}

    client = TestClient(app_test)
    img_bytes = crear_imagen_prueba(800, 600)

    # 1. Subir foto de evidencia en etapa recepción
    r = client.post(
        "/api/ordenes/SRV-2026-00001/fotos",
        data={"etapa": "recepcion", "descripcion": "Equipo con rayón en carcasa"},
        files=[("archivos", ("foto1.png", img_bytes, "image/png"))],
    )
    assert r.status_code == 200
    datos = r.json()
    assert datos["ok"] is True
    assert len(datos["fotos"]) == 1
    assert datos["fotos"][0]["etapa"] == "recepcion"
    assert datos["fotos"][0]["archivo_url"].startswith("/uploads/ordenes/SRV-2026-00001/")

    # 2. Rechazar etapa inválida
    r_inv = client.post(
        "/api/ordenes/SRV-2026-00001/fotos",
        data={"etapa": "invalida"},
        files=[("archivos", ("foto1.png", img_bytes, "image/png"))],
    )
    assert r_inv.status_code == 422

    # 3. Enviar foto en el chat como cliente
    r_chat = client.post(
        "/api/public/seguimiento/SRV-2026-00001/mensajes/foto?telefono=5512345678",
        data={"texto": "Envío foto del cargador"},
        files={"archivo": ("cargador.png", img_bytes, "image/png")},
    )
    assert r_chat.status_code == 200
    assert r_chat.json()["archivo_url"].startswith("/uploads/chat/SRV-2026-00001/")
    assert len(mensajes_mem) == 1

    # 4. Eliminar foto de evidencia
    foto_id = datos["fotos"][0]["id"]
    r_del = client.delete(f"/api/ordenes/SRV-2026-00001/fotos/{foto_id}")
    assert r_del.status_code == 200
    assert len(fotos_mem) == 0
