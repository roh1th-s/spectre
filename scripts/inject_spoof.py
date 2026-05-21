import argparse

import httpx


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inject GPS spoof via API")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--offset-nm", type=float, default=2.0, help="GPS latitude offset (nm)")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    url = f"{args.base_url}/inject/spoof"
    resp = httpx.post(url, params={"offset_nm": args.offset_nm}, timeout=5.0)
    resp.raise_for_status()
    print(resp.json())


if __name__ == "__main__":
    main()
