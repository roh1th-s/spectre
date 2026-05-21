"""Simple UDP FDM listener using flightgear_python.FDMConnection.

Listens for FlightGear native FDM on the given host/port and prints
received tuples (as JSON) when FDM updates arrive. Minimal — no
controllers/controls wiring included.

Example:
    python fg_fdm_listener.py --host localhost --port 5501
"""
import argparse
import json
import math
import time
from datetime import datetime, timezone

from flightgear_python.fg_if import FDMConnection


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def build_arg_parser():
    p = argparse.ArgumentParser(description="Listen for FlightGear FDM via FDMConnection")
    p.add_argument("--host", default="localhost", help="FlightGear host to connect to")
    p.add_argument("--port", type=int, default=5500, help="FlightGear FDM UDP port to connect to")
    p.add_argument("--poll-interval", type=float, default=0.01, help="Poll interval (s)")
    p.add_argument("--debug", action="store_true", help="Enable debug logging")
    p.add_argument("--raw", action="store_true", help="Print full raw FDM payload")
    p.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return p


def build_sim_sample(payload):
    """Build a compact sim_data-style payload from the raw FDM dict."""
    def get(*names):
        for name in names:
            if name in payload:
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
        "timestamp": utc_now_iso(),
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


def main():
    args = build_arg_parser().parse_args()

    fdm_conn = FDMConnection()

    # callback runs in child process; forward parsed FDM to parent via event_pipe
    def fdm_callback(fdm_data, event_pipe):
        if args.debug:
            try:
                print("[fdm_callback] invoked; event_pipe=%r" % (event_pipe,))
            except Exception:
                pass
        try:
            event_pipe.child_send(fdm_data)
        except Exception:
            pass
        return fdm_data

    # connect_rx returns an event pipe used to poll/receive FDM updates
    fdm_event_pipe = fdm_conn.connect_rx(args.host, args.port, fdm_callback)

    # start internal receive thread/loop if provided
    try:
        fdm_conn.start()
    except Exception:
        # some implementations may start automatically
        pass

    try:
        print(f"Listening for FDM on {args.host}:{args.port} (debug={args.debug})")
        if fdm_event_pipe is None:
            print("Warning: connect_rx returned None for event pipe")
        while True:
            try:
                polled = False
                if fdm_event_pipe is not None:
                    polled = fdm_event_pipe.parent_poll()
                    if polled:
                        pkt = fdm_event_pipe.parent_recv()
                        if pkt == (True,):
                            # startup handshake from child process
                            if args.debug:
                                print("[event] child started")
                            continue
                        if args.debug:
                            print("[event] parent_recv ->", repr(pkt))
                        try:
                            fdm_payload = dict(pkt)
                        except Exception:
                            fdm_payload = pkt
                        if args.raw:
                            out = {"timestamp": utc_now_iso(), "fdm": fdm_payload}
                        else:
                            out = build_sim_sample(fdm_payload)

                        if args.pretty:
                            print(json.dumps(out, indent=2, sort_keys=True))
                        else:
                            print(json.dumps(out))
                else:
                    # If no event pipe, sleep and continue — callback may still run
                    if args.debug:
                        print("No event pipe; waiting for callback-driven output")
                # small sleep to avoid busy loop
            except Exception as e:
                if args.debug:
                    print("Event loop error:", e)
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if hasattr(fdm_conn, "stop"):
                fdm_conn.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
