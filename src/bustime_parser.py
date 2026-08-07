"""Parse BusTime getpatterns JSON into the same shape schema as GTFS shapes.

Input file format (written by the CTA Tracker `patterns:update` script):

    {
      "routes": {
        "1": [
          { "pid": "8085", "rtdir": "Northbound", "ln": "24973",
            "pt": [
              { "seq": "1", "lat": "41.83", "lon": "-87.62", "typ": "S",
                "stpid": "15314", "stpnm": "35th Street & Indiana", "pdist": "0" },
              { "seq": "2", "lat": "41.83", "lon": "-87.62", "typ": "W" }
            ] }
        ]
      }
    }

Each pattern becomes a shape whose id is "{rt}:{pid}". Stop points (typ == "S")
are surfaced as stop_usage so stop-excursion removal works without stop_times.txt.
"""

import json

import pandas as pd

SHAPE_COLUMNS = ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"]


def parse_bustime_patterns(json_path: str) -> tuple[dict, dict, dict]:
    """Returns (shapes, route_mapping, stop_usage) matching the GTFS parser contract."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    shapes: dict[str, pd.DataFrame] = {}
    route_mapping: dict[str, list[str]] = {}
    stop_usage: dict[str, list[dict]] = {}

    for rt, patterns in data.get("routes", {}).items():
        shape_ids: list[str] = []
        for pattern in patterns:
            shape_id = f"{rt}:{pattern['pid']}"
            shape_ids.append(shape_id)
            points = pattern.get("pt", [])
            rows = []
            stops = []
            for i, pt in enumerate(points, start=1):
                rows.append(
                    {
                        "shape_id": shape_id,
                        "shape_pt_sequence": i,
                        "shape_pt_lat": float(pt["lat"]),
                        "shape_pt_lon": float(pt["lon"]),
                    }
                )
                if pt.get("typ") == "S":
                    stops.append(
                        {
                            "stop_id": str(pt.get("stpid", "")),
                            "stop_name": str(pt.get("stpnm", "")),
                            "stop_sequence": i,
                            "lat": float(pt["lat"]),
                            "lon": float(pt["lon"]),
                        }
                    )
            shapes[shape_id] = pd.DataFrame(rows, columns=SHAPE_COLUMNS)
            if stops:
                stop_usage[shape_id] = stops
        route_mapping[rt] = shape_ids

    return shapes, route_mapping, stop_usage
