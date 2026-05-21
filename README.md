# SPECTRE Prototyping

This repository contains basic prototyping code for our submission to Airbus Fly Your Ideas Challenge 2026. It's an anomaly detection and predictive maintenance tool that streams simulated data from a flight sim (FlightGear), runs the detection pipeline, and surfaces alerts and maintenance logs over WebSocket to a dashboard.

## Quickstart
1. Install dependencies (Python 3.10+ recommended).
2. Train the model:
	- `python -m spectre.train`
3. Launch FlightGear:
	- `fg_launch.bat`
4. Start the backend:
	- `uvicorn spectre.pipeline:app --reload`
5. Open the dashboard:
	- `dashboard/index.html`

## Inject test events
- GPS spoof: `python scripts/inject_spoof.py`
- Airspeed drift: `python scripts/inject_drift.py`



