from fastapi import WebSocket, WebSocketDisconnect

salas: dict[str, set[WebSocket]] = {}


async def manejar_ws(ws: WebSocket, folio: str):
    await ws.accept()
    salas.setdefault(folio, set()).add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        salas.get(folio, set()).discard(ws)


async def avisar(folio: str):
    for s in list(salas.get(folio, [])):
        try:
            await s.send_text("refresh")
        except Exception:
            salas.get(folio, set()).discard(s)
