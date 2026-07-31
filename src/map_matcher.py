import math
from dataclasses import dataclass, field
from typing import Optional

import requests
from shapely.geometry import LineString, shape


def _compute_bearings(coords: list[tuple[float, float]], bearing_range: int = 45) -> str:
    """
    Computes compass bearings (0-360 degrees) between consecutive (lon, lat) coordinates.
    Returns an OSRM bearings parameter string: '{bearing},{range};{bearing},{range}...'
    """
    bearings = []
    n = len(coords)
    if n < 2:
        return ";".join([f"0,{bearing_range}"] * n)

    for i in range(n):
        if i < n - 1:
            p1 = coords[i]
            p2 = coords[i + 1]
        else:
            p1 = coords[i - 1]
            p2 = coords[i]

        lon1, lat1 = math.radians(p1[0]), math.radians(p1[1])
        lon2, lat2 = math.radians(p2[0]), math.radians(p2[1])

        dlon = lon2 - lon1
        y = math.sin(dlon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)

        bearing_rad = math.atan2(y, x)
        bearing_deg = (math.degrees(bearing_rad) + 360.0) % 360.0

        bearings.append(f"{int(round(bearing_deg))},{bearing_range}")

    return ";".join(bearings)


@dataclass
class MatchResult:
    """Result of an OSRM map matching request."""
    success: bool = False
    geometry: Optional[LineString] = None
    confidences: list = field(default_factory=list)
    distance_meters: float = 0.0
    osm_nodes: list = field(default_factory=list)
    error: str = ""


class OSRMMapMatcher:
    def __init__(
        self,
        base_url: str = "http://localhost:5000",
        profile: str = "bus",
        snap_radius_meters: float = 15.0,
        use_bearings: bool = True,
        bearing_range: int = 45,
    ):
        """
        Args:
            base_url: Base URL of the OSRM instance.
            profile: Routing profile (e.g., 'bus', 'driving').
            snap_radius_meters: Search radius for snapping points to the road network.
            use_bearings: Pass directional heading constraints to OSRM.
            bearing_range: Allowed directional heading variance in degrees (+/- range).
        """
        self.base_url = base_url.rstrip('/')
        self.profile = profile
        self.snap_radius_meters = snap_radius_meters
        self.use_bearings = use_bearings
        self.bearing_range = bearing_range

    def match_coords(self, coords: list[tuple[float, float]]) -> MatchResult:
        """
        Matches a preprocessed trace to the road network. Returns the centerline
        geometry plus diagnostics.
        """
        if len(coords) < 2:
            return MatchResult(success=False, error="At least 2 coordinates are required")

        coords_str = ";".join([f"{lon:.5f},{lat:.5f}" for lon, lat in coords])
        url = f"{self.base_url}/match/v1/{self.profile}/{coords_str}"

        radius_str = str(int(self.snap_radius_meters))
        params = {
            "geometries": "geojson",
            "overview": "full",
            "radiuses": ";".join([radius_str] * len(coords)),
            "gaps": "ignore",
            "annotations": "nodes,distance",
        }
        if self.use_bearings:
            params["bearings"] = _compute_bearings(coords, bearing_range=self.bearing_range)

        response = requests.get(url, params=params)

        if response.status_code != 200:
            return MatchResult(success=False, error=f"OSRM HTTP error {response.status_code}: {response.text[:200]}")

        data = response.json()
        if data.get("code") != "Ok" or not data.get("matchings"):
            return MatchResult(success=False, error=f"OSRM map matching failed: {data.get('code')}")

        # With a cleaned trace OSRM returns a single matching; pick the highest
        # confidence one defensively.
        best = max(data["matchings"], key=lambda m: m.get("confidence", 0.0))

        geom = shape(best["geometry"])
        if not isinstance(geom, LineString) or len(geom.coords) < 2:
            return MatchResult(success=False, error="No usable matched geometry returned")

        nodes: list = []
        for leg in best.get("legs", []):
            annotation = leg.get("annotation", {})
            if "nodes" in annotation:
                nodes.extend(annotation["nodes"])

        return MatchResult(
            success=True,
            geometry=geom,
            confidences=[float(best.get("confidence", 0.0))],
            distance_meters=float(best.get("distance", 0.0)),
            osm_nodes=nodes,
        )
