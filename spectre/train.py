import math
import pickle
import random
from dataclasses import dataclass

import numpy as np
import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler


@dataclass
class SimConfig:
    samples: int = 1800
    dt_s: float = 1.0
    lat0_deg: float = 37.6
    lon0_deg: float = -122.3
    heading_deg: float = 90.0
    airspeed_kt: float = 250.0
    alt_ft: float = 35000.0
    irs_drift_nm_per_hr: float = 1.0
    irs_noise_sigma_deg: float = 0.00003


def kt_to_nm_per_s(kt: float) -> float:
    return kt / 3600.0


def nm_to_deg_lat(nm: float) -> float:
    return nm / 60.0


def nm_to_deg_lon(nm: float, lat_deg: float) -> float:
    return nm / (60.0 * math.cos(math.radians(lat_deg)))


def simulate_normal_flight(cfg: SimConfig) -> np.ndarray:
    rng = np.random.default_rng(42)
    lat = cfg.lat0_deg
    lon = cfg.lon0_deg
    irs_lat = lat
    irs_lon = lon

    samples = []
    speed_nm_s = kt_to_nm_per_s(cfg.airspeed_kt)
    drift_nm_s = cfg.irs_drift_nm_per_hr / 3600.0

    for _ in range(cfg.samples):
        # True motion eastbound (heading 090)
        dlon = nm_to_deg_lon(speed_nm_s * cfg.dt_s, lat)
        lon += dlon

        # GPS measurements with small noise
        gps_lat = lat + rng.normal(0.0, 0.00001)
        gps_lon = lon + rng.normal(0.0, 0.00001)

        # IRS dead-reckoning with drift and noise
        irs_lon += nm_to_deg_lon((speed_nm_s + drift_nm_s) * cfg.dt_s, irs_lat)
        irs_lat += 0.0
        irs_lat += rng.normal(0.0, cfg.irs_noise_sigma_deg)
        irs_lon += rng.normal(0.0, cfg.irs_noise_sigma_deg)

        alt = cfg.alt_ft + rng.normal(0.0, 30.0)
        airspeed = cfg.airspeed_kt + rng.normal(0.0, 2.0)
        heading = cfg.heading_deg + rng.normal(0.0, 0.5)

        samples.append(
            [
                gps_lat,
                gps_lon,
                alt,
                airspeed,
                heading,
                irs_lat,
                irs_lon,
            ]
        )

    return np.array(samples, dtype=np.float32)


class GRUAutoencoder(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int = 16, num_layers: int = 1):
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers, batch_first=True)
        self.out = nn.Linear(hidden_dim, input_dim)

    def forward(self, x):
        h, _ = self.gru(x)
        return self.out(h)


def make_sequences(data: np.ndarray, seq_len: int) -> np.ndarray:
    seqs = []
    for i in range(seq_len - 1, len(data)):
        seqs.append(data[i - seq_len + 1 : i + 1])
    return np.array(seqs, dtype=np.float32)


def main():
    random.seed(42)
    np.random.seed(42)
    torch.manual_seed(42)

    cfg = SimConfig()
    seq_len = 30

    data = simulate_normal_flight(cfg)
    scaler = StandardScaler()
    data_scaled = scaler.fit_transform(data)

    seqs = make_sequences(data_scaled, seq_len)
    x = torch.tensor(seqs, dtype=torch.float32)

    model = GRUAutoencoder(input_dim=x.shape[-1], hidden_dim=16, num_layers=1)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    loss_fn = nn.MSELoss()

    model.train()
    for _ in range(20):
        optimizer.zero_grad()
        out = model(x)
        loss = loss_fn(out, x)
        loss.backward()
        optimizer.step()

    model.eval()
    with torch.no_grad():
        recon = model(x)
        per_seq_err = torch.mean((recon - x) ** 2, dim=(1, 2)).cpu().numpy()

    threshold = float(per_seq_err.mean() + 3.0 * per_seq_err.std())

    torch.save(
        {
            "input_dim": x.shape[-1],
            "hidden_dim": 16,
            "num_layers": 1,
            "state_dict": model.state_dict(),
        },
        "gru_model.pt",
    )

    with open("scaler.pkl", "wb") as f:
        pickle.dump({"scaler": scaler, "threshold": threshold}, f)

    print(f"threshold={threshold:.6f}")


if __name__ == "__main__":
    main()
