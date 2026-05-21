import asyncio
import json
from typing import Any, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .engine import ClientMessage, PipelineEngine
from .fg_udp import FlightGearUDPSource


FG_HOST = "127.0.0.1"
FG_PORT = 5500


class WebSocketHub:
    def __init__(self) -> None:
        self.clients = []
        self.lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self.lock:
            self.clients.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self.lock:
            if ws in self.clients:
                self.clients.remove(ws)

    async def broadcast(self, payload: ClientMessage) -> None:
        async with self.lock:
            targets = list(self.clients)
        for ws in targets:
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                await self.disconnect(ws)


app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = PipelineEngine()
hub = WebSocketHub()
udp_source = FlightGearUDPSource(host=FG_HOST, port=FG_PORT, emit_interval_s=1.0)


@app.on_event("startup")
async def on_startup() -> None:
    engine.load_gru()
    engine.set_broadcaster(hub.broadcast)
    asyncio.create_task(udp_source.run(engine.process_sample, engine.set_flightgear_connected))


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)


@app.post("/inject/spoof")
async def inject_spoof(offset_nm: float = 2.0) -> Dict[str, Any]:
    await engine.inject_spoof(float(offset_nm))
    return {"ok": True}


@app.post("/inject/drift")
async def inject_drift(rate: float = 0.02) -> Dict[str, Any]:
    await engine.inject_drift(float(rate))
    return {"ok": True}


@app.post("/inject/reset")
async def inject_reset() -> Dict[str, Any]:
    await engine.inject_reset()
    return {"ok": True}


@app.post("/alert/clear")
async def alert_clear() -> Dict[str, Any]:
    await engine.alert_clear()
    return {"ok": True}


@app.get("/status")
async def status() -> Dict[str, Any]:
    return await engine.status()
