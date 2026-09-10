#!/usr/bin/env python3
"""Download OurAirports airports.csv into ./data/airports.csv for global search."""
from __future__ import annotations

import urllib.request
from pathlib import Path

URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OUT = Path(__file__).resolve().parents[1] / "data" / "airports.csv"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {URL} → {OUT}")
    urllib.request.urlretrieve(URL, OUT)
    print(f"Done ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
