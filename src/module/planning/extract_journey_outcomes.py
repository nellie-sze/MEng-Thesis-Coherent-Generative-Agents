"""
Build agents_5_journey_outcomes.json from SUMO trip/person outputs.

Example:
python src/module/planning/extract_journey_outcomes.py \
  --agents-path results/scenario_runs/9_euro_ticket/gta/day1/agents_4_route_descriptions.json \
  --tripinfo-path results/scenario_runs/9_euro_ticket/sumo/day1/tripinfo.xml \
  --personinfo-path results/scenario_runs/9_euro_ticket/sumo/day1/personinfo.xml \
  --base-config config_wedding_sumo

"""

import argparse
import csv
import importlib
import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from model.agent import Agent
from model.journey_outcome import JourneyOutcome
from util.logging import log_info
from util.storage import Storage
from util.time import time_to_seconds


DEFAULT_PT_CAPACITIES = {
    "bus": 60.0,
    "tram": 120.0,
    "subway": 300.0,
    "light_rail": 180.0,
    "train": 400.0,
    "ferry": 200.0,
    "taxi": 4.0,
    "uamtaxi": 4.0,
}

JOURNEY_OUTCOMES_CSV_HEADERS = [
    "agent_id",
    "route_id",
    "leg_index",
    "MOT",
    "timeLoss",
    "crowding",
    "delay",
]


def recursively_parse_json(obj):
    if isinstance(obj, str):
        try:
            return recursively_parse_json(json.loads(obj))
        except (json.JSONDecodeError, TypeError):
            return obj
    if isinstance(obj, list):
        return [recursively_parse_json(item) for item in obj]
    if isinstance(obj, dict):
        return {key: recursively_parse_json(value) for key, value in obj.items()}
    return obj


def load_agents(path: Path) -> list[Agent]:
    parsed = recursively_parse_json(path.read_text(encoding="utf-8"))
    return [Agent.from_json(agent_data) for agent_data in parsed]


def load_base_config(config_name: str) -> dict:
    config_module = importlib.import_module("config.sim_config")
    try:
        return deepcopy(getattr(config_module, config_name))
    except AttributeError as exc:
        raise ValueError(f"Unknown base config {config_name!r}.") from exc


def resolve_path(path_str: str | None) -> Path | None:
    if not path_str:
        return None
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (PROJECT_ROOT / path).resolve()


def route_leg_index(route_id) -> int:
    last4 = str(route_id)[-4:].zfill(4)
    return int(last4[:2])


def try_parse_trip_id(trip_id: str) -> tuple[int, int] | None:
    parts = str(trip_id).split("_", 1)
    if len(parts) != 2:
        return None
    agent_id_str, leg_index_str = parts
    if not agent_id_str.isdigit() or not leg_index_str.isdigit():
        return None
    return int(agent_id_str), int(leg_index_str)


def parse_tripinfo(tripinfo_path: Path) -> dict[tuple[int, int], dict]:
    if not tripinfo_path.exists():
        raise FileNotFoundError(f"Missing tripinfo file: {tripinfo_path}")

    root = ET.parse(tripinfo_path).getroot()
    results = {}
    for tripinfo in root.findall("tripinfo"):
        parsed_trip_id = try_parse_trip_id(tripinfo.attrib["id"])
        if parsed_trip_id is None:
            continue
        agent_id, leg_index = parsed_trip_id
        results[(agent_id, leg_index)] = {
            "arrival_time_seconds": float(tripinfo.attrib["arrival"]),
            "duration_seconds": float(tripinfo.attrib.get("duration", 0.0)),
            "waiting_time_seconds": float(tripinfo.attrib.get("waitingTime", 0.0)),
            "time_loss_seconds": float(tripinfo.attrib.get("timeLoss", 0.0)),
            "source": "tripinfo",
        }
    return results


def parse_personinfo(personinfo_path: Path) -> tuple[dict[tuple[int, int], dict], dict[str, list[tuple[float, int]]]]:
    if not personinfo_path.exists():
        raise FileNotFoundError(f"Missing personinfo file: {personinfo_path}")

    root = ET.parse(personinfo_path).getroot()
    results = {}
    occupancy_events = defaultdict(list)

    for personinfo in root.findall("personinfo"):
        parsed_trip_id = try_parse_trip_id(personinfo.attrib["id"])
        if parsed_trip_id is None:
            continue
        agent_id, leg_index = parsed_trip_id
        stages = list(personinfo)

        if stages:
            final_arrival = float(stages[-1].attrib["arrival"])
        else:
            final_arrival = float(personinfo.attrib["depart"]) + float(personinfo.attrib["duration"])

        rides = []
        for stage in stages:
            if stage.tag != "ride":
                continue
            vehicle_id = stage.attrib["vehicle"]
            depart = float(stage.attrib["depart"])
            arrival = float(stage.attrib["arrival"])
            rides.append({
                "vehicle_id": vehicle_id,
                "depart": depart,
                "arrival": arrival,
            })
            occupancy_events[vehicle_id].append((depart, +1))
            occupancy_events[vehicle_id].append((arrival, -1))

        results[(agent_id, leg_index)] = {
            "arrival_time_seconds": final_arrival,
            "duration_seconds": float(personinfo.attrib.get("duration", 0.0)),
            "waiting_time_seconds": float(personinfo.attrib.get("waitingTime", 0.0)),
            "time_loss_seconds": float(personinfo.attrib.get("timeLoss", 0.0)),
            "rides": rides,
            "source": "personinfo",
        }

    return results, occupancy_events


def parse_vehicle_capacities(xml_paths: list[Path], capacity_overrides: dict[str, float]) -> tuple[dict[str, float], dict[str, str]]:
    vtype_capacities = dict(capacity_overrides)
    vehicle_types = {}

    for xml_path in xml_paths:
        if xml_path is None or not xml_path.exists():
            continue
        root = ET.parse(xml_path).getroot()

        for vtype in root.findall(".//vType"):
            vtype_id = vtype.attrib.get("id")
            if not vtype_id:
                continue
            person_capacity = vtype.attrib.get("personCapacity")
            if person_capacity is not None:
                vtype_capacities[vtype_id] = float(person_capacity)

        for tag_name in ("trip", "vehicle"):
            for vehicle in root.findall(f".//{tag_name}"):
                vehicle_id = vehicle.attrib.get("id")
                vehicle_type = vehicle.attrib.get("type")
                if vehicle_id and vehicle_type:
                    vehicle_types[vehicle_id] = vehicle_type

    return vtype_capacities, vehicle_types


def build_occupancy_segments(events: list[tuple[float, int]]) -> list[tuple[float, float, int]]:
    if not events:
        return []

    ordered = sorted(events, key=lambda item: (item[0], -item[1]))
    segments = []
    occupancy = 0
    previous_time = None

    for current_time, delta in ordered:
        if previous_time is not None and current_time > previous_time:
            segments.append((previous_time, current_time, occupancy))
        occupancy += delta
        previous_time = current_time

    return segments


def interval_average_crowding(
    segments: list[tuple[float, float, int]],
    depart: float,
    arrival: float,
    capacity: float,
) -> float | None:
    if capacity <= 0 or arrival <= depart:
        return None

    weighted_sum = 0.0
    covered_duration = 0.0
    for segment_start, segment_end, occupancy in segments:
        overlap_start = max(depart, segment_start)
        overlap_end = min(arrival, segment_end)
        if overlap_end <= overlap_start:
            continue
        duration = overlap_end - overlap_start
        weighted_sum += duration * (occupancy / capacity)
        covered_duration += duration

    if covered_duration == 0:
        return None
    return weighted_sum / covered_duration


def compute_pt_crowding(
    rides: list[dict],
    vehicle_segments: dict[str, list[tuple[float, float, int]]],
    vehicle_types: dict[str, str],
    vtype_capacities: dict[str, float],
) -> float:
    if not rides:
        return 0.0

    weighted_sum = 0.0
    total_duration = 0.0

    for ride in rides:
        vehicle_id = ride["vehicle_id"]
        vehicle_type = vehicle_types.get(vehicle_id)
        capacity = vtype_capacities.get(vehicle_id)
        if capacity is None and vehicle_type is not None:
            capacity = vtype_capacities.get(vehicle_type)
        if capacity is None:
            continue

        depart = ride["depart"]
        arrival = ride["arrival"]
        duration = arrival - depart
        if duration <= 0:
            continue

        average = interval_average_crowding(
            vehicle_segments.get(vehicle_id, []),
            depart,
            arrival,
            capacity,
        )
        if average is None:
            continue

        weighted_sum += average * duration
        total_duration += duration

    if total_duration == 0:
        return 0.0
    return weighted_sum / total_duration


def build_simulation_metrics(
    tripinfo_path: Path,
    personinfo_path: Path,
    capacity_files: list[Path],
    capacity_overrides: dict[str, float],
) -> dict[tuple[int, int], dict]:
    trip_metrics = parse_tripinfo(tripinfo_path)
    person_metrics, occupancy_events = parse_personinfo(personinfo_path)
    if not trip_metrics and not person_metrics:
        raise ValueError(
            "No usable journey records found in either SUMO output: "
            f"tripinfo={tripinfo_path}, personinfo={personinfo_path}"
        )
    vtype_capacities, vehicle_types = parse_vehicle_capacities(capacity_files, capacity_overrides)

    vehicle_segments = {
        vehicle_id: build_occupancy_segments(events)
        for vehicle_id, events in occupancy_events.items()
    }

    combined = dict(trip_metrics)
    combined.update(person_metrics)

    for metrics in combined.values():
        metrics["crowding"] = compute_pt_crowding(
            metrics.get("rides", []),
            vehicle_segments,
            vehicle_types,
            vtype_capacities,
        )

    return combined


def build_journey_outcome(location_change, metrics: dict | None) -> JourneyOutcome:
    scheduled_arrival = time_to_seconds(location_change.to_task.time)
    actual_arrival = metrics["arrival_time_seconds"] if metrics else None

    delay = None
    if actual_arrival is not None and scheduled_arrival is not None:
        delay = round((actual_arrival - scheduled_arrival) / 60.0, 2)

    crowding = round(metrics.get("crowding", 0.0), 4) if metrics else 0.0
    time_loss = None
    if metrics:
        duration_seconds = metrics.get("duration_seconds", 0.0)
        if duration_seconds and duration_seconds > 0:
            time_loss = round(metrics.get("time_loss_seconds", 0.0) / duration_seconds, 4)

    return JourneyOutcome(
        route_id=location_change.route_id,
        from_task=location_change.from_task,
        from_building=location_change.from_building,
        to_task=location_change.to_task,
        to_building=location_change.to_building,
        decision=location_change.decision,
        arrival_time=actual_arrival,
        delay=delay,
        crowding=crowding,
        time_loss=time_loss,
    )


def derive_capacity_files(config: dict) -> list[Path]:
    files = [resolve_path(config.get("v_types_file")), resolve_path(config.get("pt_vehicles_file"))]

    for fleet_file in (config.get("taxi_fleet_files") or {}).values():
        files.append(resolve_path(fleet_file))

    deduped = []
    seen = set()
    for path in files:
        if path is None:
            continue
        normalized = str(path)
        if normalized in seen:
            continue
        deduped.append(path)
        seen.add(normalized)
    return deduped


def write_outcomes_csv(rows: list[dict[str, object]], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=JOURNEY_OUTCOMES_CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def _load_metrics(
    tripinfo_path: Path,
    personinfo_path: Path,
    config: dict,
    pt_capacity_overrides: dict[str, float] | None,
) -> dict[tuple[int, int], dict]:
    capacity_overrides = dict(DEFAULT_PT_CAPACITIES)
    if pt_capacity_overrides:
        capacity_overrides.update(pt_capacity_overrides)

    return build_simulation_metrics(
        tripinfo_path=tripinfo_path,
        personinfo_path=personinfo_path,
        capacity_files=derive_capacity_files(config),
        capacity_overrides=capacity_overrides,
    )


def _build_outcomes(agents: list[Agent], simulation_metrics: dict[tuple[int, int], dict]) -> list[dict[str, object]]:
    csv_rows = []
    for agent in agents:
        journey_outcomes = []
        for location_change in agent.location_changes or []:
            leg_index = route_leg_index(location_change.route_id)
            metrics = simulation_metrics.get((agent.id, leg_index))
            if metrics is None:
                continue
            journey_outcome = build_journey_outcome(location_change, metrics)
            journey_outcomes.append(journey_outcome)
            csv_rows.append({
                "agent_id": agent.id,
                "route_id": location_change.route_id,
                "leg_index": leg_index,
                "MOT": (location_change.decision or {}).get("means_of_transport"),
                "timeLoss": journey_outcome.time_loss,
                "crowding": journey_outcome.crowding,
                "delay": journey_outcome.delay,
            })
        agent.journey_outcomes = journey_outcomes
    return csv_rows


def _write_outputs(results_dir: Path, agents: list[Agent], csv_rows: list[dict[str, object]]) -> Path:
    output_path = results_dir / "agents_5_journey_outcomes.json"
    csv_output_path = results_dir / "journey_outcomes.csv"
    write_outcomes_csv(csv_rows, csv_output_path)
    storage = Storage(str(results_dir))
    storage.write_agents(agents, "5_journey_outcomes")
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract per-leg journey outcomes from SUMO outputs and write agents_5_journey_outcomes.json."
    )
    parser.add_argument(
        "--agents-path",
        required=True,
        help="Path to agents_4_route_descriptions.json. The output will be written beside it as agents_5_journey_outcomes.json.",
    )
    parser.add_argument("--tripinfo-path", required=True, help="Path to SUMO tripinfo.xml.")
    parser.add_argument("--personinfo-path", required=True, help="Path to SUMO personinfo.xml.")
    parser.add_argument(
        "--base-config",
        default="config_wedding_sumo",
        help="Name of the base config from src/config/sim_config.py used to locate vehicle definition files.",
    )
    parser.add_argument(
        "--pt-capacity-overrides",
        default=None,
        help='JSON object of per-vType person capacities, e.g. \'{"bus":80,"tram":180}\'.',
    )
    return parser


def extract_journey_outcomes(
    agents_path: Path | str,
    tripinfo_path: Path | str,
    personinfo_path: Path | str,
    config: dict | None = None,
    base_config_name: str = "config_wedding_sumo",
    pt_capacity_overrides: dict[str, float] | None = None,
) -> Path:

    agents_path = Path(agents_path).resolve()
    if not agents_path.exists():
        raise FileNotFoundError(f"Missing {agents_path}")
    if agents_path.name != "agents_4_route_descriptions.json":
        raise ValueError("agents-path must point to agents_4_route_descriptions.json")

    results_dir = agents_path.parent
    config = deepcopy(config) if config is not None else load_base_config(base_config_name)
    tripinfo_path = Path(tripinfo_path).resolve()
    personinfo_path = Path(personinfo_path).resolve()
    agents = load_agents(agents_path)
    simulation_metrics = _load_metrics(
        tripinfo_path,
        personinfo_path,
        config,
        pt_capacity_overrides,
    )
    csv_rows = _build_outcomes(agents, simulation_metrics)
    output_path = _write_outputs(results_dir, agents, csv_rows)
    log_info(f"Wrote {output_path}")
    return output_path


def main():
    args = build_parser().parse_args()
    pt_capacity_overrides = json.loads(args.pt_capacity_overrides) if args.pt_capacity_overrides else None
    extract_journey_outcomes(
        agents_path=args.agents_path,
        tripinfo_path=args.tripinfo_path,
        personinfo_path=args.personinfo_path,
        base_config_name=args.base_config,
        pt_capacity_overrides=pt_capacity_overrides,
    )


if __name__ == "__main__":
    main()
