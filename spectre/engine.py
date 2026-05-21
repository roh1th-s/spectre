import asyncio
import math
import pickle
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Deque, Dict, List, Literal, Optional, TypedDict

import numpy as np
import torch
import torch.nn as nn
from filterpy.kalman import KalmanFilter


EARTH_RADIUS_NM = 3440.065


class Scores(TypedDict):
    mahalanobis: float
    cusum: float
    gru_reconstruction_error: float
    gps_irs_divergence_nm: float
    layers_flagged: int


class GpsInfo(TypedDict):
    expected_lat_deg: float
    expected_lon_deg: float
    observed_lat_deg: float
    observed_lon_deg: float
    divergence_nm: float


class HeadingInfo(TypedDict):
    target_heading_deg: float
    current_heading_deg: float
    error_deg: float


class AlertBody(TypedDict):
    timestamp: str
    severity: Literal["HIGH", "MAINTENANCE", "INFO"]
    type: Literal["SPOOF", "ANOMALY", "DRIFT"]
    subsystem: str
    message: str
    detail: str
    scores: Scores
    gps: GpsInfo
    heading: HeadingInfo


class AlertMessage(AlertBody):
    event: Literal["ALERT"]


ClientMessage = AlertMessage


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(ts: Optional[str]) -> str:
    if not ts:
        return now_iso()
    if ts.endswith("Z"):
        return ts
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return now_iso()


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_r, lon1_r, lat2_r, lon2_r = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_NM * c


def nm_to_deg_lat(nm: float) -> float:
    return nm / 60.0


def nm_to_deg_lon(nm: float, lat_deg: float) -> float:
    return nm / (60.0 * math.cos(math.radians(lat_deg)))


def kt_to_nm_per_s(kt: float) -> float:
    return kt / 3600.0


def get_first(payload: Dict[str, Any], *names: str) -> Optional[float]:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return None


def heading_error_deg(current_deg: float, target_deg: float) -> float:
    diff = (current_deg - target_deg + 180.0) % 360.0 - 180.0
    return diff


class SyntheticIRS:
    def __init__(self) -> None:
        self.lat: Optional[float] = None
        self.lon: Optional[float] = None
        self.heading_deg: Optional[float] = None
        self.drift_nm_per_hr = 0.5
        self.noise_sigma_deg = 0.00002

    def _smooth_heading(self, new_heading_deg: float, alpha: float = 0.2) -> float:
        if self.heading_deg is None:
            self.heading_deg = new_heading_deg
            return new_heading_deg
        prev_rad = math.radians(self.heading_deg)
        new_rad = math.radians(new_heading_deg)
        x = (1.0 - alpha) * math.cos(prev_rad) + alpha * math.cos(new_rad)
        y = (1.0 - alpha) * math.sin(prev_rad) + alpha * math.sin(new_rad)
        smoothed = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
        self.heading_deg = smoothed
        return smoothed

    def update(self, lat: float, lon: float, airspeed_kt: float, heading_deg: float, dt: float) -> None:
        if self.lat is None or self.lon is None:
            self.lat = lat
            self.lon = lon
            return
        heading_deg = self._smooth_heading(heading_deg)
        speed_nm_s = kt_to_nm_per_s(airspeed_kt)
        drift_nm_s = self.drift_nm_per_hr / 3600.0
        dist_nm = (speed_nm_s + drift_nm_s) * dt

        heading_rad = math.radians(heading_deg)
        north_nm = dist_nm * math.cos(heading_rad)
        east_nm = dist_nm * math.sin(heading_rad)

        self.lat += nm_to_deg_lat(north_nm)
        self.lon += nm_to_deg_lon(east_nm, self.lat)

        self.lat += np.random.normal(0.0, self.noise_sigma_deg)
        self.lon += np.random.normal(0.0, self.noise_sigma_deg)


class EKFNav:
    def __init__(self) -> None:
        self.kf = KalmanFilter(dim_x=4, dim_z=2)
        self.kf.H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)
        self.kf.R = np.diag([1e-6, 1e-6])
        self.kf.P = np.diag([1e-3, 1e-3, 1e-2, 1e-2])
        self.initialized = False

    def update(self, lat: float, lon: float, dt: float) -> np.ndarray:
        if not self.initialized:
            self.kf.x = np.array([lat, lon, 0.0, 0.0], dtype=float)
            self.initialized = True
            return self.kf.x

        self.kf.F = np.array(
            [[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]],
            dtype=float,
        )
        q = 1e-6
        self.kf.Q = np.diag([q, q, q * 10, q * 10])
        self.kf.predict()
        self.kf.update(np.array([lat, lon], dtype=float))
        return self.kf.x


def state_speed_kt(state: np.ndarray) -> float:
    lat = float(state[0])
    vlat_deg_s = float(state[2])
    vlon_deg_s = float(state[3])
    v_north_nm_s = vlat_deg_s * 60.0
    v_east_nm_s = vlon_deg_s * 60.0 * math.cos(math.radians(lat))
    speed_nm_s = math.hypot(v_north_nm_s, v_east_nm_s)
    return speed_nm_s * 3600.0


class MahalanobisDetector:
    def __init__(self, warmup: int = 120) -> None:
        self.warmup = warmup
        self.samples: List[np.ndarray] = []
        self.mean: Optional[np.ndarray] = None
        self.inv_cov: Optional[np.ndarray] = None

    def update(self, residual: np.ndarray) -> float:
        if self.inv_cov is None:
            self.samples.append(residual)
            if len(self.samples) >= self.warmup:
                data = np.vstack(self.samples)
                self.mean = data.mean(axis=0)
                cov = np.cov(data.T)
                cov += np.eye(cov.shape[0]) * 1e-6
                self.inv_cov = np.linalg.inv(cov)
            return 0.0
        diff = residual - self.mean
        return float(math.sqrt(diff.T @ self.inv_cov @ diff))


class CUSUMDetector:
    def __init__(self, threshold: float = 5.0, drift: float = 0.5) -> None:
        self.threshold = threshold
        self.drift = drift
        self.score = 0.0
        self.sustain_s = 0.0

    def update(self, x: float, dt: float) -> Dict[str, Any]:
        self.score = max(0.0, self.score + (x - self.drift))
        flagged = self.score > self.threshold
        if flagged:
            self.sustain_s += dt
        else:
            self.sustain_s = 0.0
        return {"score": self.score, "flagged": flagged, "sustained": self.sustain_s >= 60.0}


class GRUAutoencoder(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int = 16, num_layers: int = 1) -> None:
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers, batch_first=True)
        self.out = nn.Linear(hidden_dim, input_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h, _ = self.gru(x)
        return self.out(h)


@dataclass
class InjectionState:
    spoof_offset_nm: Optional[float] = None
    spoof_end_ts: Optional[float] = None
    drift_rate_pct_per_s: Optional[float] = None
    drift_start_ts: Optional[float] = None


@dataclass
class PipelineState:
    ekf: EKFNav = field(default_factory=EKFNav)
    irs: SyntheticIRS = field(default_factory=SyntheticIRS)
    mahal: MahalanobisDetector = field(default_factory=MahalanobisDetector)
    cusum: CUSUMDetector = field(default_factory=CUSUMDetector)
    window: Deque[List[float]] = field(default_factory=lambda: deque(maxlen=30))
    window_len: int = 30
    scaler: Optional[Any] = None
    gru_model: Optional[GRUAutoencoder] = None
    gru_threshold: Optional[float] = None
    last_sample_ts: Optional[float] = None
    last_flag_ts: Optional[float] = None
    last_status_log_ts: Optional[float] = None
    alt_baseline: Optional[float] = None
    target_heading_deg: float = 270.0
    samples_seen: int = 0
    drift_sustain_s: float = 0.0
    last_alert_sig: Optional[str] = None
    scores: Scores = field(default_factory=lambda: {
        "mahalanobis": 0.0,
        "cusum": 0.0,
        "gru_reconstruction_error": 0.0,
        "gps_irs_divergence_nm": 0.0,
        "layers_flagged": 0,
    })
    active_alert: Optional[AlertBody] = None
    flightgear_connected: bool = False
    injections: InjectionState = field(default_factory=InjectionState)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class PipelineEngine:
    def __init__(self) -> None:
        self.state = PipelineState()
        self.broadcaster: Optional[Callable[[ClientMessage], Awaitable[None]]] = None

    def set_broadcaster(self, broadcaster: Callable[[ClientMessage], Awaitable[None]]) -> None:
        self.broadcaster = broadcaster

    async def set_flightgear_connected(self, connected: bool) -> None:
        async with self.state.lock:
            self.state.flightgear_connected = connected

    def load_gru(self) -> None:
        try:
            with open("scaler.pkl", "rb") as f:
                data = pickle.load(f)
            self.state.scaler = data["scaler"]
            self.state.gru_threshold = float(data["threshold"])
            ckpt = torch.load("gru_model.pt", map_location="cpu")
            self.state.gru_model = GRUAutoencoder(
                input_dim=ckpt["input_dim"],
                hidden_dim=ckpt["hidden_dim"],
                num_layers=ckpt["num_layers"],
            )
            self.state.gru_model.load_state_dict(ckpt["state_dict"])
            self.state.gru_model.eval()
        except Exception:
            self.state.gru_model = None
            self.state.scaler = None
            self.state.gru_threshold = None

    async def process_sample(self, payload: Dict[str, Any], source_ts: Optional[str] = None) -> None:
        async with self.state.lock:
            ts_iso = parse_iso(source_ts or payload.get("timestamp"))
            ts_now = time.monotonic()
            if self.state.last_sample_ts is None:
                dt = 1.0
            else:
                dt = max(0.2, min(5.0, ts_now - self.state.last_sample_ts))
            self.state.last_sample_ts = ts_now

            gps_lat = get_first(payload, "gps_lat_deg", "lat_deg", "lat")
            gps_lon = get_first(payload, "gps_lon_deg", "lon_deg", "lon")
            alt_ft = get_first(payload, "alt_ft", "altitude")
            heading_deg = get_first(payload, "heading_deg", "psi_deg", "heading")
            airspeed_kt = get_first(payload, "indicated_airspeed_kt", "true_airspeed_kt", "vcas")
            groundspeed_kt = get_first(payload, "groundspeed_kt")

            if gps_lat is None or gps_lon is None or airspeed_kt is None or heading_deg is None:
                return

            self.state.samples_seen += 1

            heading_err = heading_error_deg(float(heading_deg), float(self.state.target_heading_deg))

            inj = self.state.injections
            if inj.spoof_end_ts and time.time() > inj.spoof_end_ts:
                inj.spoof_offset_nm = None
                inj.spoof_end_ts = None
            if inj.spoof_offset_nm is not None:
                gps_lat = float(gps_lat) + nm_to_deg_lat(inj.spoof_offset_nm)

            if inj.drift_rate_pct_per_s is not None:
                if inj.drift_start_ts is None:
                    inj.drift_start_ts = time.time()
                elapsed = time.time() - inj.drift_start_ts
                factor = 1.0 + (inj.drift_rate_pct_per_s * elapsed) / 100.0
                airspeed_kt = float(airspeed_kt) * factor

            irs_speed_kt = float(groundspeed_kt) if groundspeed_kt is not None else float(airspeed_kt)
            injection_active = inj.spoof_offset_nm is not None or inj.drift_rate_pct_per_s is not None
            allow_alerts = injection_active

            ekf_state = self.state.ekf.update(float(gps_lat), float(gps_lon), dt)
            self.state.irs.update(float(gps_lat), float(gps_lon), irs_speed_kt, float(heading_deg), dt)

            irs_lat = self.state.irs.lat if self.state.irs.lat is not None else float(gps_lat)
            irs_lon = self.state.irs.lon if self.state.irs.lon is not None else float(gps_lon)
            divergence_nm = haversine_nm(float(gps_lat), float(gps_lon), float(irs_lat), float(irs_lon))

            ekf_speed = state_speed_kt(ekf_state)
            airspeed_resid = float(airspeed_kt) - ekf_speed

            if self.state.alt_baseline is None:
                self.state.alt_baseline = float(alt_ft) if alt_ft is not None else 0.0
            if alt_ft is not None:
                self.state.alt_baseline = 0.95 * self.state.alt_baseline + 0.05 * float(alt_ft)
                alt_resid = float(alt_ft) - self.state.alt_baseline
            else:
                alt_resid = 0.0

            residual = np.array([divergence_nm, airspeed_resid, alt_resid], dtype=float)
            mahal = self.state.mahal.update(residual)
            cusum = self.state.cusum.update(divergence_nm, dt)

            if inj.drift_rate_pct_per_s is not None:
                resid_pct = abs(airspeed_resid) / max(float(airspeed_kt), 1.0) * 100.0
                if resid_pct >= 0.1:
                    self.state.drift_sustain_s += dt
                else:
                    self.state.drift_sustain_s = 0.0
            else:
                self.state.drift_sustain_s = 0.0

            gru_error = 0.0
            gru_flag = False
            if self.state.gru_model and self.state.scaler:
                self.state.window.append(
                    [
                        float(gps_lat),
                        float(gps_lon),
                        float(alt_ft or 0.0),
                        float(airspeed_kt),
                        float(heading_deg),
                        float(irs_lat),
                        float(irs_lon),
                    ]
                )
                if allow_alerts and len(self.state.window) == self.state.window_len:
                    data = np.array(self.state.window, dtype=np.float32)
                    data_scaled = self.state.scaler.transform(data)
                    x = torch.tensor(data_scaled, dtype=torch.float32).unsqueeze(0)
                    with torch.no_grad():
                        recon = self.state.gru_model(x)
                        gru_error = float(torch.mean((recon - x) ** 2).item())
                    if self.state.gru_threshold is not None:
                        gru_flag = gru_error > self.state.gru_threshold

            mahal_flag = mahal > 3.0
            cusum_flag = bool(cusum["flagged"]) if allow_alerts else False
            cusum_sustained = bool(cusum["sustained"]) if allow_alerts else False
            layers_flagged = int(mahal_flag) + int(cusum_flag) + int(gru_flag)

            self.state.scores = {
                "mahalanobis": float(mahal),
                "cusum": float(cusum["score"]),
                "gru_reconstruction_error": float(gru_error),
                "gps_irs_divergence_nm": float(divergence_nm),
                "layers_flagged": int(layers_flagged),
            }

            any_flag = (layers_flagged > 0 or cusum_sustained) if allow_alerts else False
            if any_flag:
                self.state.last_flag_ts = time.monotonic()

            gps_info = {
                "expected_lat_deg": float(irs_lat),
                "expected_lon_deg": float(irs_lon),
                "observed_lat_deg": float(gps_lat),
                "observed_lon_deg": float(gps_lon),
                "divergence_nm": float(divergence_nm),
            }
            heading_info = {
                "target_heading_deg": float(self.state.target_heading_deg),
                "current_heading_deg": float(heading_deg),
                "error_deg": float(heading_err),
            }

            candidate = None
            if not allow_alerts:
                candidate = None
            elif cusum_sustained or (inj.drift_rate_pct_per_s is not None and self.state.drift_sustain_s >= 3.0):
                if inj.drift_rate_pct_per_s is not None and self.state.drift_sustain_s >= 3.0:
                    drift_msg = f"Airspeed sensor drift - residual {abs(airspeed_resid):.1f}kt sustained"
                else:
                    drift_msg = "Airspeed sensor drift - schedule inspection"
                candidate = {
                    "timestamp": ts_iso,
                    "severity": "MAINTENANCE",
                    "type": "DRIFT",
                    "subsystem": "Sensors",
                    "message": drift_msg,
                    "detail": drift_msg,
                    "scores": self.state.scores,
                    "gps": gps_info,
                    "heading": heading_info,
                }
            elif layers_flagged >= 2:
                is_spoof = divergence_nm >= 1.0 or cusum_flag
                detail = (
                    f"GPS/IRS divergence {divergence_nm:.1f}nm - cross-check QRH Nav-3"
                    if is_spoof
                    else "Multi-layer anomaly detected"
                )
                candidate = {
                    "timestamp": ts_iso,
                    "severity": "HIGH",
                    "type": "SPOOF" if is_spoof else "ANOMALY",
                    "subsystem": "Navigation",
                    "message": detail,
                    "detail": detail,
                    "scores": self.state.scores,
                    "gps": gps_info,
                    "heading": heading_info,
                }

            def sev_rank(sev: str) -> int:
                return {"INFO": 1, "HIGH": 2, "MAINTENANCE": 3}.get(sev, 0)

            alert_changed = False
            if candidate:
                if self.state.active_alert is None or sev_rank(candidate["severity"]) > sev_rank(
                    self.state.active_alert["severity"]
                ):
                    self.state.active_alert = candidate
                    alert_changed = True
            else:
                if self.state.active_alert and self.state.last_flag_ts is not None:
                    if time.monotonic() - self.state.last_flag_ts >= 60.0:
                        self.state.active_alert = None
                        alert_changed = True
                        self.state.last_alert_sig = None

            if alert_changed and self.broadcaster and self.state.active_alert:
                alert_sig = f"{self.state.active_alert['type']}|{self.state.active_alert['subsystem']}"
                if alert_sig != self.state.last_alert_sig:
                    msg = {"event": "ALERT", **self.state.active_alert}
                    await self.broadcaster(msg)
                    self.state.last_alert_sig = alert_sig

            now_log = time.monotonic()
            if self.state.last_status_log_ts is None or (now_log - self.state.last_status_log_ts) >= 10.0:
                lat_out = float(gps_lat)
                lon_out = float(gps_lon)
                alt_out = float(alt_ft) if alt_ft is not None else 0.0
                spd_out = float(airspeed_kt)
                hdg_out = float(heading_deg)
                print(
                    f"[DATA] lat={lat_out:.6f} lon={lon_out:.6f} hdg={hdg_out:.1f} "
                    f"alt={alt_out:.0f}ft v={spd_out:.1f}kt"
                )
                self.state.last_status_log_ts = now_log

            if self.state.active_alert:
                print(
                    f"[ALERT] {self.state.active_alert['severity']} {self.state.active_alert['type']} "
                    f"mahal={mahal:.1f} cusum={cusum['score']:.1f} gru={gru_error:.3f} "
                    f"div={divergence_nm:.2f}nm head_err={heading_err:.1f}deg"
                )

    async def inject_spoof(self, offset_nm: float) -> None:
        async with self.state.lock:
            self.state.injections.spoof_offset_nm = float(offset_nm)
            self.state.injections.spoof_end_ts = time.time() + 30.0

    async def inject_drift(self, rate: float) -> None:
        async with self.state.lock:
            self.state.injections.drift_rate_pct_per_s = float(rate)
            self.state.injections.drift_start_ts = time.time()

    async def inject_reset(self) -> None:
        async with self.state.lock:
            self.state.injections = InjectionState()

    async def alert_clear(self) -> None:
        async with self.state.lock:
            self.state.active_alert = None
            self.state.last_flag_ts = None

    async def status(self) -> Dict[str, Any]:
        async with self.state.lock:
            active_inj = []
            if self.state.injections.spoof_offset_nm is not None:
                active_inj.append("spoof")
            if self.state.injections.drift_rate_pct_per_s is not None:
                active_inj.append("drift")
            return {
                "active_alert": self.state.active_alert,
                "active_injections": active_inj,
                "scores": self.state.scores,
                "flightgear_connected": self.state.flightgear_connected,
            }
