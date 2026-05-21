import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from flightgear_python.fg_if import FDMConnection


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_sim_sample(payload: Dict[str, Any]) -> Dict[str, Any]:
    def get(*names: str) -> Optional[float]:
        for name in names:
            if name in payload and payload[name] is not None:
                return payload[name]
        return None

    lat_rad = get("lat_rad", "latitude", "lat")
    lon_rad = get("lon_rad", "longitude", "lon")
    psi_rad = get("psi_rad", "psi")
    alt_m = get("alt_m", "altitude")
    v_north = get("v_north_ft_per_s", "v_north")
    v_east = get("v_east_ft_per_s", "v_east")
    vcas = get("vcas")

    lat_deg = float(lat_rad) * 180.0 / math.pi if lat_rad is not None else None
    lon_deg = float(lon_rad) * 180.0 / math.pi if lon_rad is not None else None
    heading_deg = float(psi_rad) * 180.0 / math.pi if psi_rad is not None else None
    alt_ft = float(alt_m) * 3.28084 if alt_m is not None else None

    groundspeed_kt = None
    if v_north is not None and v_east is not None:
        speed_ft_s = math.hypot(float(v_north), float(v_east))
        groundspeed_kt = speed_ft_s / 1.687809857

    return {
        "timestamp": now_iso(),
        "lat_deg": lat_deg,
        "lon_deg": lon_deg,
        "alt_ft": alt_ft,
        "heading_deg": heading_deg,
        "true_airspeed_kt": vcas,
        "indicated_airspeed_kt": vcas,
        "groundspeed_kt": groundspeed_kt,
        "gps_lat_deg": lat_deg,
        "gps_lon_deg": lon_deg,
    }


class FlightGearUDPSource:
    def __init__(
        self,
        host: str,
        port: int,
        poll_interval: float = 0.01,
        stale_after_s: float = 5.0,
        emit_interval_s: float = 1.0,
    ) -> None:
        self.host = host
        self.port = port
        self.poll_interval = poll_interval
        self.stale_after_s = stale_after_s
        self.emit_interval_s = emit_interval_s
        self.conn: Optional[FDMConnection] = None
        self.event_pipe = None
        self.last_rx_ts = 0.0

    def _callback(self, fdm_data: Any, event_pipe: Any) -> Any:
        try:
            event_pipe.child_send(fdm_data)
        except Exception:
            pass
        return fdm_data

    def start(self) -> None:
        self.conn = FDMConnection()
        self.event_pipe = self.conn.connect_rx(self.host, self.port, self._callback)
        try:
            self.conn.start()
        except Exception:
            pass

    async def run(
        self,
        on_payload: Callable[[Dict[str, Any]], Awaitable[None]],
        set_connected: Callable[[bool], Awaitable[None]],
    ) -> None:
        self.start()
        connected = False
        last_emit = 0.0
        self.last_rx_ts = time.monotonic()

        while True:
            try:
                if self.event_pipe is None:
                    if connected:
                        await set_connected(False)
                        connected = False
                    await asyncio.sleep(self.poll_interval)
                    continue

                polled = self.event_pipe.parent_poll()
                if polled:
                    pkt = self.event_pipe.parent_recv()
                    if pkt == (True,):
                        continue
                    try:
                        raw = dict(pkt)
                    except Exception:
                        raw = pkt
                    payload = build_sim_sample(raw)
                    now = time.monotonic()
                    self.last_rx_ts = now
                    if not connected:
                        await set_connected(True)
                        connected = True
                    if now - last_emit >= self.emit_interval_s:
                        await on_payload(payload)
                        last_emit = now
                else:
                    now = time.monotonic()
                    if connected and (now - self.last_rx_ts) >= self.stale_after_s:
                        await set_connected(False)
                        connected = False
                    await asyncio.sleep(self.poll_interval)
            except Exception:
                if connected:
                    await set_connected(False)
                    connected = False
                await asyncio.sleep(1.0)
