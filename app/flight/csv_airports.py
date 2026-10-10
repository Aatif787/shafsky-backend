"""
Global airport search from airports.csv only.

This module must NEVER write to, read from, or mix with supported_airports /
airport_services. It is a reference list for unrestricted origin/destination fields.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _csv_candidates() -> List[Path]:
    here = Path(__file__).resolve()
    roots = [
        here.parents[3],  # workspace: .../shafksy
        here.parents[2],  # shafsky-backend-main
        Path.cwd(),
        Path.cwd().parent,
    ]
    names = [
        Path("tools") / "airports.csv",
        Path("data") / "airports.csv",
        Path("airports.csv"),
        Path("shafsky-backend-main") / "data" / "airports.csv",
        Path("shafsky-backend-main") / "airports.csv",
        Path("shafsky-frontend-main") / "public" / "data" / "airports.csv",
    ]
    out: List[Path] = []
    for root in roots:
        for name in names:
            out.append((root / name).resolve())
    seen = set()
    unique = []
    for p in out:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def resolve_airports_csv_path() -> Path:
    for path in _csv_candidates():
        if path.is_file():
            return path
    raise FileNotFoundError(
        "airports.csv not found. Expected ./data/airports.csv or ./airports.csv for global search only."
    )


def _public_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "code": row["code"],
        "name": row["name"],
        "city": row["city"],
        "country": row["country"],
        "is_supported": False,
    }


POPULAR_GLOBAL_HUBS: Tuple[str, ...] = (
    "DEL", "BOM", "DXB", "LHR", "SIN", "JFK", "DOH", "BLR", "HYD", "MAA",
    "FRA", "CDG", "AMS", "IST", "BKK", "KUL", "HKG", "HND", "SYD", "LAX",
    "ORD", "SFO", "YYZ", "ZRH", "AUH", "JED", "RUH", "MUC", "FCO", "BCN"
)


@lru_cache(maxsize=1)
def load_global_airports() -> Tuple[List[Dict[str, str]], List[Dict[str, str]], Dict[str, Dict[str, str]]]:
    """Returns (all IATA airports, large_airport subset, by_code index). CSV only."""
    path = resolve_airports_csv_path()
    rows: List[Dict[str, str]] = []
    large: List[Dict[str, str]] = []
    by_code: Dict[str, Dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            iata = (raw.get("iata_code") or "").strip().upper()
            if len(iata) != 3 or not iata.isalpha():
                continue
            kind = (raw.get("type") or "").strip().lower()
            if kind in ("closed", "heliport", "seaplane_base", "balloonport"):
                continue
            scheduled = (raw.get("scheduled_service") or "").strip().lower()
            row = {
                "code": iata,
                "name": (raw.get("name") or f"{iata} Airport").strip(),
                "city": (raw.get("municipality") or "").strip(),
                "country": (raw.get("iso_country") or "").strip(),
                "type": kind,
                "scheduled": scheduled,
            }
            rows.append(row)
            if iata not in by_code:
                by_code[iata] = row
            if kind == "large_airport" and scheduled == "yes":
                large.append(row)
    return rows, large, by_code


def preload_global_airports() -> int:
    rows, _large, _by_code = load_global_airports()
    return len(rows)


def search_global_csv_airports(query: str, limit: int = 30) -> List[Dict[str, str]]:
    """Search CSV only. Does not consult the Shafsky supported-airport database."""
    airports, large, by_code = load_global_airports()
    q = (query or "").strip().upper()
    if not q:
        hubs = [_public_row(by_code[c]) for c in POPULAR_GLOBAL_HUBS if c in by_code]
        hub_codes = {h["code"] for h in hubs}
        extras = [_public_row(row) for row in large if row["code"] not in hub_codes]
        return (hubs + extras)[:limit]

    exact: List[Dict[str, str]] = []
    starts_large: List[Dict[str, str]] = []
    starts_other: List[Dict[str, str]] = []
    contains_large: List[Dict[str, str]] = []
    contains_other: List[Dict[str, str]] = []

    for row in airports:
        code = row["code"]
        name = row["name"].upper()
        city = row["city"].upper()
        is_large = row.get("type") == "large_airport"
        if code == q:
            exact.append(row)
        elif code.startswith(q) or city.startswith(q) or name.startswith(q):
            if is_large:
                starts_large.append(row)
            else:
                starts_other.append(row)
        elif q in name or q in city or q in code:
            if is_large:
                contains_large.append(row)
            else:
                contains_other.append(row)

    ranked = exact + starts_large + starts_other + contains_large + contains_other
    seen = set()
    unique: List[Dict[str, str]] = []
    for row in ranked:
        if row["code"] in seen:
            continue
        seen.add(row["code"])
        unique.append(_public_row(row))
        if len(unique) >= limit:
            break
    return unique
