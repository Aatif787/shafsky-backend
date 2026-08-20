"""
Global airport search from airports.csv only.

This module must NEVER write to, read from, or mix with supported_airports /
airport_services. It is a reference list for unrestricted origin/destination fields.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple


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


def _public_row(row: Dict[str, str]) -> Dict[str, str]:
    return {
        "code": row["code"],
        "name": row["name"],
        "city": row["city"],
        "country": row["country"],
        "is_supported": False,
    }


@lru_cache(maxsize=1)
def load_global_airports() -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Returns (all IATA airports, large_airport subset). CSV only."""
    path = resolve_airports_csv_path()
    rows: List[Dict[str, str]] = []
    large: List[Dict[str, str]] = []
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
            if kind == "large_airport" and scheduled == "yes":
                large.append(row)
    return rows, large


def preload_global_airports() -> int:
    rows, _large = load_global_airports()
    return len(rows)


def search_global_csv_airports(query: str, limit: int = 30) -> List[Dict[str, str]]:
    """Search CSV only. Does not consult the Shafsky supported-airport database."""
    airports, large = load_global_airports()
    q = (query or "").strip().upper()
    if not q:
        return [_public_row(row) for row in large[:limit]]

    exact: List[Dict[str, str]] = []
    starts: List[Dict[str, str]] = []
    contains: List[Dict[str, str]] = []

    for row in airports:
        code = row["code"]
        name = row["name"].upper()
        city = row["city"].upper()
        if code == q:
            exact.append(row)
        elif code.startswith(q) or city.startswith(q) or name.startswith(q):
            starts.append(row)
        elif q in name or q in city or q in code:
            contains.append(row)

    ranked = exact + starts + contains
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
