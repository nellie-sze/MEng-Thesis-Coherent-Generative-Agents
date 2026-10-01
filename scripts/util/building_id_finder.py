import argparse
import importlib
import math
import sys
from copy import deepcopy
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config.vehicle_config import vehicle_configs
from model.vehicle import VehicleRegistry
from module.action.closest_location_choice import ClosestLocationChoice
from module.action.sumo.sumo_adapter import SumoAdapter


DEFAULT_BASE_CONFIG = "config_wedding_sumo"
DEFAULT_LIMIT = 20
DEFAULT_SAMPLE_POINTS = 12
DEFAULT_SHORTLIST_FACTOR = 5
DEFAULT_INTERMODAL_ARRIVAL = "18:00"
DEFAULT_SAMPLE_SEED = 42
RESIDENTIAL_SAMPLE_WEIGHT = 4.0
REPRESENTATIVE_EXECUTION_VEHICLES = {
    "pedestrian": ["pedestrian"],
    "vehicle": ["passenger", "bicycle"],
    "intermodal": ["public_transport_single"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only helper to discover candidate event polygon_ids in a configured study area "
            "and summarize venue accessibility by execution type."
        )
    )
    parser.add_argument(
        "--base-config",
        "--config",
        dest="base_config",
        default=DEFAULT_BASE_CONFIG,
        help="Name of the base config from src/config/config.py. Defaults to config_wedding_sumo.",
    )
    parser.add_argument(
        "--building-type",
        help="Optional building tag value to match across configured candidate attributes.",
    )
    parser.add_argument("--lat", type=float, help="Latitude for the geographic search center.")
    parser.add_argument("--lon", type=float, help="Longitude for the geographic search center.")
    parser.add_argument(
        "--radius-km",
        type=float,
        help="Radius in kilometers around --lat/--lon to search within.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum number of ranked candidates to print. Defaults to {DEFAULT_LIMIT}.",
    )
    parser.add_argument(
        "--sample-points",
        type=int,
        default=DEFAULT_SAMPLE_POINTS,
        help=(
            "Number of representative study-area sample points used for inbound/outbound "
            f"accessibility checks. Defaults to {DEFAULT_SAMPLE_POINTS}."
        ),
    )
    parser.add_argument(
        "--intermodal-arrival-time",
        default=DEFAULT_INTERMODAL_ARRIVAL,
        help=(
            "Representative HH:MM arrival time used for public transport checks. "
            f"Defaults to {DEFAULT_INTERMODAL_ARRIVAL}."
        ),
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    geo_values = [args.lat, args.lon, args.radius_km]
    if any(value is not None for value in geo_values) and not all(value is not None for value in geo_values):
        raise ValueError("Provide --lat, --lon, and --radius-km together for geographic search.")
    if args.radius_km is not None and args.radius_km <= 0:
        raise ValueError("--radius-km must be greater than zero.")
    if args.limit <= 0:
        raise ValueError("--limit must be greater than zero.")
    if args.sample_points <= 0:
        raise ValueError("--sample-points must be greater than zero.")
    if args.building_type is None and args.radius_km is None:
        raise ValueError("Provide at least one filter: --building-type and/or --lat/--lon/--radius-km.")


def load_base_config(config_name: str) -> dict:
    config_module = importlib.import_module("config.config")
    try:
        return deepcopy(getattr(config_module, config_name))
    except AttributeError as exc:
        raise ValueError(f"Unknown base config {config_name!r}.") from exc


def parse_hhmm_to_seconds(value: str) -> int:
    try:
        hour_str, minute_str = value.split(":", maxsplit=1)
        hours = int(hour_str)
        minutes = int(minute_str)
    except ValueError as exc:
        raise ValueError(f"Invalid HH:MM value: {value!r}.") from exc

    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"Invalid HH:MM value: {value!r}.")
    return hours * 3600 + minutes * 60


def centroid_series(frame: gpd.GeoDataFrame) -> gpd.GeoSeries:
    return frame.geometry.centroid


def find_matching_attributes(urban_sampler: ClosestLocationChoice, building_type: str) -> list[str]:
    matches = []
    for attribute in urban_sampler.candidate_attributes:
        if attribute in urban_sampler.buildings.columns:
            values = urban_sampler.buildings[attribute]
            if values.eq(building_type).any():
                matches.append(attribute)
    return matches


def filter_candidates(
    buildings: gpd.GeoDataFrame,
    urban_sampler: ClosestLocationChoice,
    building_type: str | None,
    lat: float | None,
    lon: float | None,
    radius_km: float | None,
) -> tuple[gpd.GeoDataFrame, list[str]]:
    candidates = buildings.copy()
    matched_attributes = []

    if building_type is not None:
        matched_attributes = find_matching_attributes(urban_sampler, building_type)
        if matched_attributes:
            type_mask = pd.Series(False, index=candidates.index)
            for attribute in matched_attributes:
                type_mask = type_mask | candidates[attribute].eq(building_type)
            candidates = candidates[type_mask]
        else:
            candidates = candidates.iloc[0:0]

    if lat is not None and lon is not None and radius_km is not None:
        center_geo = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326")
        center_local = center_geo.to_crs(candidates.crs).iloc[0]
        distances_m = centroid_series(candidates).distance(center_local)
        candidates = candidates[distances_m <= radius_km * 1000].copy()

    return candidates, matched_attributes


def build_output_frame(
    candidates: gpd.GeoDataFrame,
    matched_attributes: list[str],
    building_type: str | None,
    lat: float | None,
    lon: float | None,
) -> gpd.GeoDataFrame:
    if candidates.empty:
        return candidates

    result = candidates.copy()
    result["centroid"] = centroid_series(result)
    result["area_m2"] = result.geometry.area.fillna(0.0)

    geo_centroids = gpd.GeoDataFrame(geometry=result["centroid"], crs=result.crs).to_crs(epsg=4326).geometry
    result["lon"] = geo_centroids.x
    result["lat"] = geo_centroids.y

    tag_strings = []
    for _, row in result.iterrows():
        tags = []
        if building_type is not None and matched_attributes:
            for attribute in matched_attributes:
                value = row.get(attribute)
                if pd.notna(value) and value == building_type:
                    tags.append(f"{attribute}={value}")
        else:
            for attribute in ["building", "amenity", "office", "shop", "craft", "category"]:
                value = row.get(attribute)
                if pd.notna(value):
                    tags.append(f"{attribute}={value}")
        tag_strings.append(", ".join(tags[:4]) if tags else "no matching tags")
    result["matched_info"] = tag_strings

    if lat is not None and lon is not None:
        result["distance_km"] = result.apply(
            lambda row: haversine_km(lon, lat, row["lon"], row["lat"]),
            axis=1,
        )
    else:
        result["distance_km"] = math.nan

    return result


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    earth_radius_km = 6371.0
    lon1_rad, lat1_rad, lon2_rad, lat2_rad = map(math.radians, [lon1, lat1, lon2, lat2])
    delta_lon = lon2_rad - lon1_rad
    delta_lat = lat2_rad - lat1_rad
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    return earth_radius_km * 2 * math.asin(math.sqrt(a))


def choose_shortlist(candidates: gpd.GeoDataFrame, limit: int, has_geo_filter: bool) -> tuple[gpd.GeoDataFrame, int]:
    if candidates.empty:
        return candidates, 0

    shortlist_size = min(len(candidates), max(limit * DEFAULT_SHORTLIST_FACTOR, limit))
    if has_geo_filter and "distance_km" in candidates.columns:
        shortlist = candidates.nsmallest(shortlist_size, "distance_km")
    else:
        shortlist = candidates.nlargest(shortlist_size, "area_m2")
    return shortlist.copy(), shortlist_size


def choose_sample_points(urban_sampler: ClosestLocationChoice, sample_points: int) -> list[tuple[float, float]]:
    buildings = urban_sampler.buildings.copy()
    if buildings.empty:
        return []
    centroids = centroid_series(buildings)
    if centroids.empty:
        return []

    residential_mask = pd.Series(False, index=buildings.index)
    if "category" in buildings.columns:
        residential_mask = residential_mask | buildings["category"].eq("residential")
    if "building" in buildings.columns:
        residential_mask = residential_mask | buildings["building"].isin(["residential", "apartments", "house"])

    weights = np.ones(len(buildings), dtype=float)
    if residential_mask.any():
        weights[residential_mask.to_numpy()] = RESIDENTIAL_SAMPLE_WEIGHT
    probabilities = weights / weights.sum()

    rng = np.random.default_rng(DEFAULT_SAMPLE_SEED)
    replace = len(buildings) < sample_points
    sampled_positions = rng.choice(len(buildings), size=sample_points, replace=replace, p=probabilities)
    return [(centroids.iloc[index].x, centroids.iloc[index].y) for index in sampled_positions]


def route_exists(possible_route) -> bool:
    if possible_route is None:
        return False
    if possible_route.route in (None, [], ()):
        return False
    if possible_route.travel_time in (None, math.inf):
        return False
    return True


def probe_route(traffic_sim: SumoAdapter, vehicle, origin: tuple[float, float], destination: tuple[float, float], arrival_time: int) -> bool:
    try:
        route = traffic_sim.branch_by_execution(vehicle, origin, destination, arrival_time)
    except Exception:
        return False
    return route_exists(route)


def summarize_accessibility(
    traffic_sim: SumoAdapter,
    vehicle_registry: VehicleRegistry,
    candidate_row: pd.Series,
    sample_locations: list[tuple[float, float]],
    arrival_time: int,
) -> dict[str, dict[str, object]]:
    candidate_location = (candidate_row["centroid"].x, candidate_row["centroid"].y)
    summary = {}

    for execution_type, vehicle_names in REPRESENTATIVE_EXECUTION_VEHICLES.items():
        inbound_successes = 0
        outbound_successes = 0

        for sample_location in sample_locations:
            inbound_ok = any(
                probe_route(
                    traffic_sim,
                    vehicle_registry.get(vehicle_name),
                    sample_location,
                    candidate_location,
                    arrival_time,
                )
                for vehicle_name in vehicle_names
            )
            outbound_ok = any(
                probe_route(
                    traffic_sim,
                    vehicle_registry.get(vehicle_name),
                    candidate_location,
                    sample_location,
                    arrival_time,
                )
                for vehicle_name in vehicle_names
            )
            inbound_successes += int(inbound_ok)
            outbound_successes += int(outbound_ok)

        total = len(sample_locations)
        inbound_ratio = inbound_successes / total if total else 0.0
        outbound_ratio = outbound_successes / total if total else 0.0
        summary[execution_type] = {
            "inbound_count": inbound_successes,
            "outbound_count": outbound_successes,
            "total": total,
            "inbound_ratio": inbound_ratio,
            "outbound_ratio": outbound_ratio,
            "accessible": inbound_successes > 0 and outbound_successes > 0,
        }

    return summary


def accessibility_score(accessibility: dict[str, dict[str, object]]) -> float:
    score = 0.0
    for execution_type in ["pedestrian", "vehicle", "intermodal"]:
        summary = accessibility[execution_type]
        score += float(summary["inbound_ratio"]) + float(summary["outbound_ratio"])
        if summary["accessible"]:
            score += 0.5
    return score


def format_accessibility(summary: dict[str, dict[str, object]]) -> list[str]:
    lines = []
    for execution_type in ["pedestrian", "vehicle", "intermodal"]:
        entry = summary[execution_type]
        inbound_percent = round(float(entry["inbound_ratio"]) * 100)
        outbound_percent = round(float(entry["outbound_ratio"]) * 100)
        lines.append(
            f"{execution_type} accessibility: {inbound_percent}% in {outbound_percent}% out"
        )
    return lines


def print_header(total_matches: int) -> None:
    print("Event location helper")
    print(f"matched_candidates: {total_matches}\n")


def print_eval_context(
    args: argparse.Namespace,
    total_matches: int,
    shortlisted_matches: int,
    matched_attributes: list[str],
    sample_count: int,
) -> None:
    if total_matches != shortlisted_matches:
        print(
            f"accessibility_shortlist: evaluating {shortlisted_matches} candidates before ranking "
            f"(pre-filtered from {total_matches})"
        )
    if args.building_type is not None:
        attribute_text = ", ".join(matched_attributes) if matched_attributes else "none"
        print(f"building_type_filter: {args.building_type} (matched attributes: {attribute_text})")
    if args.radius_km is not None:
        print(
            f"geo_filter: center=({args.lat:.6f}, {args.lon:.6f}), radius_km={args.radius_km:.2f}"
        )
    print(f"study_area_sample_points: {sample_count}")
    print("")

def prompt_next_action() -> str:
    print("Choose next action:")
    print("1. eval")
    print("2. print")
    print("3. exit")

    while True:
        choice = input("> ").strip().lower()
        if choice in {"1", "eval"}:
            return "eval"
        if choice in {"2", "print"}:
            return "print"
        if choice in {"3", "exit"}:
            return "exit"
        print("Please enter eval, print, exit, or 1, 2, 3.")


def print_basic_candidates(candidates: list[dict], limit: int, has_geo_filter: bool) -> None:
    if not candidates:
        print("No candidate venues found for the provided filters.")
        return

    for rank, candidate in enumerate(candidates[:limit], start=1):
        distance_text = (
            f", distance={candidate['distance_km']:.2f}km"
            if has_geo_filter and not math.isnan(candidate["distance_km"])
            else ""
        )
        print(
            f"{rank}. polygon_id={candidate['polygon_id']}, "
            f"lat={candidate['lat']:.6f}, lon={candidate['lon']:.6f}{distance_text}"
        )
        print(f"   matched_info: {candidate['matched_info']}")
        print("")


def print_candidates(candidates: list[dict], limit: int, has_geo_filter: bool) -> None:
    if not candidates:
        print("No candidate venues found for the provided filters.")
        return

    for rank, candidate in enumerate(candidates[:limit], start=1):
        distance_text = (
            f", distance={candidate['distance_km']:.2f}km"
            if has_geo_filter and not math.isnan(candidate["distance_km"])
            else ""
        )
        print(
            f"{rank}. polygon_id={candidate['polygon_id']}, "
            f"lat={candidate['lat']:.6f}, lon={candidate['lon']:.6f}{distance_text}, "
            f"accessibility_score={candidate['accessibility_score']:.2f}"
        )
        print(f"   matched_info: {candidate['matched_info']}")
        for line in format_accessibility(candidate["accessibility"]):
            print(f"   {line}")
        print("")


def main() -> None:
    args = parse_args()
    validate_args(args)
    config = load_base_config(args.base_config)
    intermodal_arrival_time = parse_hhmm_to_seconds(args.intermodal_arrival_time)

    urban_sampler = ClosestLocationChoice(config["buildings_file"], config["taz_file"])
    filtered_candidates, matched_attributes = filter_candidates(
        urban_sampler.buildings,
        urban_sampler,
        args.building_type,
        args.lat,
        args.lon,
        args.radius_km,
    )
    prepared_candidates = build_output_frame(
        filtered_candidates,
        matched_attributes,
        args.building_type,
        args.lat,
        args.lon,
    )
    shortlist, shortlisted_count = choose_shortlist(
        prepared_candidates,
        args.limit,
        has_geo_filter=args.radius_km is not None,
    )
    sample_locations = choose_sample_points(urban_sampler, args.sample_points)

    print_header(total_matches=len(prepared_candidates))

    if shortlist.empty:
        print_candidates([], args.limit, has_geo_filter=args.radius_km is not None)
        return

    preview_candidates = shortlist.copy()
    if args.radius_km is not None and "distance_km" in preview_candidates.columns:
        preview_candidates = preview_candidates.sort_values(["distance_km", "area_m2"], ascending=[True, False])
    else:
        preview_candidates = preview_candidates.sort_values(["area_m2", "id"], ascending=[False, True])

    basic_candidates = [
        {
            "polygon_id": candidate_row["id"],
            "lat": candidate_row["lat"],
            "lon": candidate_row["lon"],
            "distance_km": candidate_row["distance_km"],
            "matched_info": candidate_row["matched_info"],
        }
        for _, candidate_row in preview_candidates.iterrows()
    ]

    next_action = prompt_next_action()
    print("")
    if next_action == "exit":
        print("Exiting without evaluating accessibility.")
        return
    if next_action == "print":
        print_basic_candidates(basic_candidates, args.limit, has_geo_filter=args.radius_km is not None)
        return
    print_eval_context(
        args,
        total_matches=len(prepared_candidates),
        shortlisted_matches=shortlisted_count,
        matched_attributes=matched_attributes,
        sample_count=len(sample_locations),
    )

    vehicle_registry = VehicleRegistry.from_configs(vehicle_configs)
    traffic_sim = SumoAdapter(
        urban_sampler,
        config["net_file"],
        config["poly_file"],
        config["v_types_file"],
        config["pt_stops_file"],
        config["pt_vehicles_file"],
        vehicle_registry,
    )

    try:
        ranked_candidates = []
        for _, candidate_row in shortlist.iterrows():
            accessibility = summarize_accessibility(
                traffic_sim,
                vehicle_registry,
                candidate_row,
                sample_locations,
                intermodal_arrival_time,
            )
            ranked_candidates.append(
                {
                    "polygon_id": candidate_row["id"],
                    "lat": candidate_row["lat"],
                    "lon": candidate_row["lon"],
                    "distance_km": candidate_row["distance_km"],
                    "matched_info": candidate_row["matched_info"],
                    "accessibility": accessibility,
                    "accessibility_score": accessibility_score(accessibility),
                    "area_m2": candidate_row["area_m2"],
                }
            )

        ranked_candidates.sort(
            key=lambda candidate: (
                -candidate["accessibility_score"],
                candidate["distance_km"] if not math.isnan(candidate["distance_km"]) else float("inf"),
                -candidate["area_m2"],
                str(candidate["polygon_id"]),
            )
        )
        print_candidates(ranked_candidates, args.limit, has_geo_filter=args.radius_km is not None)
    finally:
        traffic_sim.stop_sim()


if __name__ == "__main__":
    main()
