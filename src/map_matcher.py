import math
from dataclasses import dataclass, field
from typing import Optional

import requests
import pandas as pd
from shapely.geometry import LineString, Point, shape


def _haversine_meters(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Approximate geodesic distance in meters between two (lon, lat) points."""
    mean_lat = math.radians((p1[1] + p2[1]) / 2.0)
    dx = (p2[0] - p1[0]) * 111000.0 * math.cos(mean_lat)
    dy = (p2[1] - p1[1]) * 111000.0
    return math.hypot(dx, dy)


def _polyline_length(coords: list[tuple[float, float]]) -> float:
    """Total length in meters of a polyline defined by (lon, lat) coords."""
    return sum(_haversine_meters(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _dedupe_coords(coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove consecutive duplicate coordinates while preserving order and endpoints."""
    out = []
    for c in coords:
        if not out or c != out[-1]:
            out.append(c)
    return out


def _resample_max_gap(coords: list[tuple[float, float]], max_gap_meters: float = 300.0, max_points: int = 500) -> list[tuple[float, float]]:
    """
    Ensures that no two adjacent coordinates in coords are separated by more than effective max_gap_meters,
    while guaranteeing that the total point count never exceeds max_points.
    """
    if len(coords) < 2:
        return coords

    total_dist = _polyline_length(coords)

    # Dynamic gap budget so point count never exceeds max_points
    min_required_gap = total_dist / max(1, (max_points - 1))
    effective_gap = max(max_gap_meters, min_required_gap)

    resampled = [coords[0]]
    for i in range(len(coords) - 1):
        p1 = coords[i]
        p2 = coords[i + 1]
        dist = _haversine_meters(p1, p2)

        if dist > effective_gap:
            num_segments = int(math.ceil(dist / effective_gap))
            for k in range(1, num_segments):
                fraction = k / float(num_segments)
                interp_lon = p1[0] + fraction * (p2[0] - p1[0])
                interp_lat = p1[1] + fraction * (p2[1] - p1[1])
                resampled.append((interp_lon, interp_lat))

        resampled.append(p2)

    return resampled[:max_points]


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
class SegmentResult:
    """A single contiguous OSRM map matching result (one entry in the matchings array)."""
    geometry: LineString
    confidence: float
    distance_meters: float
    osm_nodes: list
    source_start: Optional[int]
    source_end: Optional[int]
    tracepoint_indices: list
    repaired: bool = False


@dataclass
class MatchResult:
    """Structured result of a map matching request, preserving diagnostics for downstream validation."""
    success: bool = False
    geometry: Optional[LineString] = None
    segments: list = field(default_factory=list)
    tracepoints: list = field(default_factory=list)
    confidences: list = field(default_factory=list)
    distance_meters: float = 0.0
    osm_nodes: list = field(default_factory=list)
    request_coords: list = field(default_factory=list)
    repair_count: int = 0
    error: str = ""


class OSRMMapMatcher:
    def __init__(
        self,
        base_url: str = "http://localhost:5000",
        profile: str = "bus",
        max_points: int = 500,
        max_gap_meters: float = 300.0,
        snap_radius_meters: float = 15.0,
        use_bearings: bool = True,
        bearing_range: int = 45,
        stitch_tolerance_meters: float = 25.0,
        min_confidence: float = 0.75,
        bridge_snap_radius_meters: Optional[float] = None,
        max_bridge_score: float = 250.0,
        window_context_points: int = 5,
        simplify_tolerance_meters: float = 15.0,
    ):
        """
        Initializes the MapMatcher.

        Args:
            base_url: The base URL of the OSRM instance.
            profile: The routing profile (e.g., 'bus', 'driving').
            max_points: The maximum number of coordinates allowed per request.
            max_gap_meters: Maximum allowed distance in meters between consecutive points.
            snap_radius_meters: Search radius in meters for snapping GPS points to road network.
            use_bearings: Whether to pass directional heading/bearing constraints to OSRM.
            bearing_range: Allowed directional heading variance in degrees (+/- range).
            stitch_tolerance_meters: Maximum endpoint gap in meters before two adjacent
                matching segments are considered disconnected (no artificial connector added).
            min_confidence: Per-segment confidence below which the segment is re-matched
                with continuity-constrained windowed candidates.
            bridge_snap_radius_meters: Tighter snap radius used for repair windows.
                Defaults to min(snap_radius_meters, 15.0).
            max_bridge_score: Maximum candidate score accepted for a repair; higher
                scores mean the candidate does not convincingly connect its neighbors.
            window_context_points: Number of source points to include on each side of a
                repair window so candidates overlap with known-good neighbors.
            simplify_tolerance_meters: RDP simplification tolerance applied to the
                trace before matching. GTFS bus shapes typically contain heavy GPS
                jitter that collapses OSRM match confidence to ~0 and causes wrong-road
                snaps; stripping deviations below this tolerance restores reliable
                matching while preserving real turns (which deviate far more).
        """
        self.base_url = base_url.rstrip('/')
        self.profile = profile
        self.max_points = max_points
        self.max_gap_meters = max_gap_meters
        self.snap_radius_meters = snap_radius_meters
        self.use_bearings = use_bearings
        self.bearing_range = bearing_range
        self.stitch_tolerance_meters = stitch_tolerance_meters
        self.min_confidence = min_confidence
        self.bridge_snap_radius_meters = bridge_snap_radius_meters if bridge_snap_radius_meters is not None else min(snap_radius_meters, 15.0)
        self.max_bridge_score = max_bridge_score
        self.window_context_points = window_context_points
        self.simplify_tolerance_meters = simplify_tolerance_meters

        # Scoring weights for continuity-constrained candidate selection
        self.detour_penalty = 400.0
        self.confidence_penalty = 250.0
        self.node_continuity_bonus = 300.0

    def _preprocess(self, coords: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """
        Prepare a trace for OSRM matching:
          1. Deduplicate consecutive coordinates.
          2. RDP-simplify to simplify_tolerance_meters to strip GPS jitter. Raw GTFS
             bus shapes are extremely noisy; without this step OSRM match confidence
             collapses to ~0 and the matcher snaps to wrong roads.
          3. Resample straightaways and enforce the OSRM point budget.
        """
        coords = _dedupe_coords(coords)

        # Strip GPS jitter unless explicitly disabled (simplify_tolerance_meters <= 0).
        if self.simplify_tolerance_meters > 0.0:
            tol_deg = self.simplify_tolerance_meters / 111000.0
            geom = LineString(coords)
            simplified = list(geom.simplify(tol_deg, preserve_topology=False).coords)
            if len(simplified) >= 2:
                coords = simplified

        if len(coords) > self.max_points:
            tolerance = self.simplify_tolerance_meters / 111000.0 if self.simplify_tolerance_meters > 0.0 else 0.00005
            while len(coords) > self.max_points:
                geom = LineString(coords)
                simplified = list(geom.simplify(tolerance, preserve_topology=False).coords)
                # Resample straightaways so gaps do not exceed max_gap_meters (e.g. 300m)
                coords = _resample_max_gap(simplified, max_gap_meters=self.max_gap_meters, max_points=self.max_points)

                tolerance *= 1.5
                if tolerance > 0.01:
                    break
        else:
            # Ensure straightaways are pinned at max_gap_meters max gaps
            coords = _resample_max_gap(coords, max_gap_meters=self.max_gap_meters, max_points=self.max_points)
            if len(coords) > self.max_points:
                coords = coords[:self.max_points]

        return coords

    def _request(self, coords: list[tuple[float, float]], snap_radius_meters: float) -> tuple[Optional[dict], str]:
        """Issue an OSRM match request. Returns (json_data, error_string)."""
        coords_str = ";".join([f"{lon:.5f},{lat:.5f}" for lon, lat in coords])
        url = f"{self.base_url}/match/v1/{self.profile}/{coords_str}"

        radius_str = str(int(snap_radius_meters))
        params = {
            "geometries": "geojson",
            "overview": "full",
            "radiuses": ";".join([radius_str] * len(coords)),
            "gaps": "split",
            "annotations": "nodes,distance",
        }

        if self.use_bearings:
            params["bearings"] = _compute_bearings(coords, bearing_range=self.bearing_range)

        response = requests.get(url, params=params)

        if response.status_code != 200:
            return None, f"OSRM HTTP error {response.status_code}: {response.text[:200]}"

        data = response.json()

        if data.get("code") != "Ok" or not data.get("matchings"):
            return None, f"OSRM map matching failed: {data.get('code')}"

        return data, ""

    def _build_segments(self, data: dict, coords: list[tuple[float, float]]) -> tuple[list[SegmentResult], list]:
        """
        Parse OSRM response into per-matching SegmentResults.
        With gaps=split, each matching is a contiguous trace segment; a matching's
        'indices' map its waypoints back to indices into the request coordinates.
        """
        tracepoints = data.get("tracepoints") or []
        segments: list[SegmentResult] = []

        for match in data["matchings"]:
            geom = shape(match["geometry"])
            if not isinstance(geom, LineString) or len(geom.coords) < 2:
                continue

            indices = [int(i) for i in (match.get("indices") or [])]
            nodes: list = []
            for leg in match.get("legs", []):
                annotation = leg.get("annotation", {})
                if "nodes" in annotation:
                    nodes.extend(annotation["nodes"])

            source_start = indices[0] if indices else None
            source_end = indices[-1] if indices else None

            segments.append(SegmentResult(
                geometry=geom,
                confidence=float(match.get("confidence", 0.0)),
                distance_meters=float(match.get("distance", 0.0)),
                osm_nodes=nodes,
                source_start=source_start,
                source_end=source_end,
                tracepoint_indices=indices,
            ))

        segments.sort(key=lambda s: (s.source_start if s.source_start is not None else -1,
                                     s.source_end if s.source_end is not None else -1))
        return segments, tracepoints

    def _stitch(self, segments: list[SegmentResult]) -> tuple[Optional[LineString], int]:
        """
        Concatenate segment geometries into a primary LineString.
        Segments are only joined when consecutive endpoints genuinely connect
        (within stitch_tolerance_meters). Disjoint segments are NOT connected with
        artificial straight-line connectors; the longest run is returned as the
        primary geometry and the number of disconnected runs is reported.
        """
        if not segments:
            return None, 0

        runs = []
        current = list(segments[0].geometry.coords)
        for prev, nxt in zip(segments, segments[1:]):
            gap = _haversine_meters(current[-1], list(nxt.geometry.coords)[0])
            if gap <= self.stitch_tolerance_meters:
                current.extend(list(nxt.geometry.coords)[1:])
            else:
                runs.append(LineString(current))
                current = list(nxt.geometry.coords)
        runs.append(LineString(current))

        if len(runs) == 1:
            return runs[0], 0

        longest = max(runs, key=lambda g: _polyline_length(list(g.coords)))
        return longest, len(runs) - 1

    # ------------------------------------------------------------------
    # Continuity-constrained repair
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_nodes(match: dict) -> list:
        nodes: list = []
        for leg in match.get("legs", []):
            annotation = leg.get("annotation", {})
            if "nodes" in annotation:
                nodes.extend(annotation["nodes"])
        return nodes

    def _node_continuity(self, cand_nodes: list, prev_seg: Optional[SegmentResult], next_seg: Optional[SegmentResult]) -> float:
        """
        Same-road prior: reward a candidate whose OSM node trajectory shares nodes
        with the tail of the preceding segment and the head of the following segment.
        A candidate that literally continues the same road scores up to 2.0.
        """
        s = 0.0
        if prev_seg is not None and prev_seg.osm_nodes and cand_nodes:
            prev_tail = set(prev_seg.osm_nodes[-8:])
            if any(n in prev_tail for n in cand_nodes[:4]):
                s += 1.0
        if next_seg is not None and next_seg.osm_nodes and cand_nodes:
            next_head = set(next_seg.osm_nodes[:8])
            if any(n in next_head for n in cand_nodes[-4:]):
                s += 1.0
        return s

    def _score_candidate(
        self,
        geom: LineString,
        cand_nodes: list,
        gap_points: list[tuple[float, float]],
        prev_seg: Optional[SegmentResult],
        next_seg: Optional[SegmentResult],
        confidence: float,
    ) -> float:
        """
        Score a repair-window candidate. Lower is better.

        Penalizes:
          - distance from the previous segment's end to the candidate start
          - distance from the candidate end to the next segment's start
          - detours (candidate length much longer than the source span)
          - lateral deviation of source points from the candidate
          - low confidence

        Rewards (same-road prior):
          - OSM node continuity with the neighboring accepted segments
        """
        coords_list = list(geom.coords)
        score = 0.0

        if prev_seg is not None:
            score += _haversine_meters(list(prev_seg.geometry.coords)[-1], coords_list[0])
        if next_seg is not None:
            score += _haversine_meters(coords_list[-1], list(next_seg.geometry.coords)[0])

        src_len = _polyline_length(gap_points) if len(gap_points) > 1 else 0.0
        cand_len = _polyline_length(coords_list)
        detour = (cand_len / src_len) if src_len > 1.0 else 1.0
        score += max(0.0, detour - 1.0) * self.detour_penalty

        if gap_points:
            devs = [Point(p).distance(geom) * 111000.0 for p in gap_points]
            score += sum(devs) / len(devs)

        score += (1.0 - confidence) * self.confidence_penalty
        score -= self._node_continuity(cand_nodes, prev_seg, next_seg) * self.node_continuity_bonus
        return score

    def _best_candidate(
        self,
        window_coords: list[tuple[float, float]],
        gap_points: list[tuple[float, float]],
        prev_seg: Optional[SegmentResult],
        next_seg: Optional[SegmentResult],
    ) -> Optional[tuple[LineString, list, float, float]]:
        """
        Re-match a repair window with a tighter snap radius and return the candidate
        (geometry, osm_nodes, confidence, distance) that best connects its neighbors.
        Returns None when no candidate connects convincingly (score > max_bridge_score).
        """
        matchings = self._request_matchings(window_coords, self.bridge_snap_radius_meters)
        if not matchings:
            return None

        best = None
        best_score = float('inf')
        for match in matchings:
            geom = shape(match["geometry"])
            if not isinstance(geom, LineString) or len(geom.coords) < 2:
                continue
            cand_nodes = self._collect_nodes(match)
            conf = float(match.get("confidence", 0.0))
            score = self._score_candidate(geom, cand_nodes, gap_points, prev_seg, next_seg, conf)
            if score < best_score:
                best_score = score
                best = (geom, cand_nodes, conf, float(match.get("distance", 0.0)))

        if best is None or best_score > self.max_bridge_score:
            return None
        return best

    def _request_matchings(self, window_coords: list[tuple[float, float]], snap_radius_meters: float) -> list:
        data, err = self._request(window_coords, snap_radius_meters)
        if data is None:
            return []
        return data.get("matchings", [])

    def _bridge_gaps(self, segments: list[SegmentResult], coords: list[tuple[float, float]]) -> tuple[list[SegmentResult], int]:
        """
        Try to reconnect disjoint matching segments by windowed re-matching of the
        source span between them. Never adds a straight-line artificial connector;
        a gap is only closed when a routed candidate connects both neighbors well.
        """
        if len(segments) < 2:
            return segments, 0

        ctx = self.window_context_points
        bridged: list[SegmentResult] = []
        repaired = 0

        for idx, seg in enumerate(segments):
            bridged.append(seg)
            if idx + 1 >= len(segments):
                continue
            nxt = segments[idx + 1]
            if seg.source_end is None or nxt.source_start is None:
                continue

            gap_points = coords[seg.source_end + 1:nxt.source_start]
            start = max(0, seg.source_start - ctx)
            end = min(len(coords), nxt.source_end + ctx + 1)
            window = coords[start:end]
            if len(window) < 4:
                continue

            best = self._best_candidate(window, gap_points, prev_seg=seg, next_seg=nxt)
            if best is None:
                continue

            geom, nodes, conf, distance = best
            bridged.append(SegmentResult(
                geometry=geom,
                confidence=conf,
                distance_meters=distance,
                osm_nodes=nodes,
                source_start=None,
                source_end=None,
                tracepoint_indices=[],
                repaired=True,
            ))
            repaired += 1

        return bridged, repaired

    def _refine_low_confidence(self, segments: list[SegmentResult], coords: list[tuple[float, float]]) -> tuple[list[SegmentResult], int]:
        """
        Re-match spans whose OSRM confidence is low, using windowed candidates that
        are constrained to connect the surrounding accepted segments. This is the
        'assume continued travel along the same road' repair: a wrong-road snap is
        replaced only when a candidate better connects to the known-good neighbors.
        """
        ctx = self.window_context_points
        refined: list[SegmentResult] = []
        repaired = 0

        for idx, seg in enumerate(segments):
            if seg.confidence >= self.min_confidence or seg.source_start is None or seg.source_end is None:
                refined.append(seg)
                continue

            points = coords[seg.source_start:seg.source_end + 1]
            start = max(0, seg.source_start - ctx)
            end = min(len(coords), seg.source_end + ctx + 1)
            window = coords[start:end]
            if len(window) < 4:
                refined.append(seg)
                continue

            prev_seg = refined[-1] if refined else None
            next_seg = segments[idx + 1] if idx + 1 < len(segments) else None

            keep_score = self._score_candidate(seg.geometry, seg.osm_nodes, points, prev_seg, next_seg, seg.confidence)
            best = self._best_candidate(window, points, prev_seg, next_seg)
            if best is None:
                refined.append(seg)
                continue

            geom, nodes, conf, distance = best
            cand_score = self._score_candidate(geom, nodes, points, prev_seg, next_seg, conf)
            if cand_score < keep_score:
                refined.append(SegmentResult(
                    geometry=geom,
                    confidence=conf,
                    distance_meters=distance,
                    osm_nodes=nodes,
                    source_start=seg.source_start,
                    source_end=seg.source_end,
                    tracepoint_indices=seg.tracepoint_indices,
                    repaired=True,
                ))
                repaired += 1
            else:
                refined.append(seg)

        return refined, repaired

    def match_shape(self, shape_df: pd.DataFrame) -> MatchResult:
        """
        Takes a DataFrame containing GTFS shape points and returns a structured
        MatchResult with matched geometry, per-segment details, and raw tracepoints.
        """
        if 'shape_pt_sequence' in shape_df.columns:
            shape_df = shape_df.sort_values(by='shape_pt_sequence')

        coords = list(zip(shape_df['shape_pt_lon'], shape_df['shape_pt_lat']))

        if len(coords) < 2:
            raise ValueError("At least 2 points are required for map matching.")

        coords = self._preprocess(coords)

        data, err = self._request(coords, self.snap_radius_meters)
        if data is None:
            return MatchResult(success=False, error=err, request_coords=coords)

        segments, tracepoints = self._build_segments(data, coords)
        if not segments:
            return MatchResult(success=False, error="No usable matching geometry returned", request_coords=coords)

        # Continuity-constrained repair:
        # 1) bridge source gaps between disjoint matchings with routed candidates
        # 2) re-match low-confidence spans against their known-good neighbors
        segments, gap_repairs = self._bridge_gaps(segments, coords)
        segments, refined = self._refine_low_confidence(segments, coords)
        repair_count = gap_repairs + refined

        geometry, disjoint_runs = self._stitch(segments)
        if geometry is None:
            return MatchResult(success=False, error="No matched geometry could be assembled", request_coords=coords)

        total_distance = sum(s.distance_meters for s in segments)
        all_nodes: list = []
        for s in segments:
            all_nodes.extend(s.osm_nodes)
        confidences = [s.confidence for s in segments]

        return MatchResult(
            success=True,
            geometry=geometry,
            segments=segments,
            tracepoints=tracepoints,
            confidences=confidences,
            distance_meters=round(total_distance, 1),
            osm_nodes=all_nodes,
            request_coords=coords,
            repair_count=repair_count,
            error=f"disjoint_runs={disjoint_runs}" if disjoint_runs else "",
        )
