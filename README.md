# SPECTRE Prototyping

SPECTRE is a cockpit anomaly detection prototype. It streams simulated data from flightgear, runs the detection pipeline, and surfaces alerts and maintenance logs over WebSocket.

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



