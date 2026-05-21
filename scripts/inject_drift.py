import argparse

import httpx


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inject airspeed drift via API")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--rate", type=float, default=0.02, help="Drift rate percent per second")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    url = f"{args.base_url}/inject/drift"
    resp = httpx.post(url, params={"rate": args.rate}, timeout=5.0)
    resp.raise_for_status()
    print(resp.json())


if __name__ == "__main__":
    main()
