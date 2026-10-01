""" python scripts/coherence/recover_saved_route_run.py \
  --agents4-input "results/coherence_sweep/qwen3.5-2b/00_vanilla/agents_4_route_descriptions.json" \
  --model "Qwen/Qwen3.5-2B" \
  --ablation true \
  --iterative false \
  --force-regen false \
  --guided-context false \
  --regen-decision-count 0 """

import argparse
import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected a boolean value, got {value!r}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recover CSV/summary outputs from an existing agents_4_route_descriptions.json without rerunning inference."
    )
    parser.add_argument(
        "--agents4-input",
        required=True,
        help="Path to an existing agents_4_route_descriptions.json file.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write recovered outputs to. Defaults to the parent directory of --agents4-input.",
    )
    parser.add_argument("--model", required=True, help="Model id used for the saved run.")
    parser.add_argument("--ablation", type=parse_bool, required=True, help="Coherence setting used for the saved run.")
    parser.add_argument("--iterative", type=parse_bool, required=True, help="Coherence setting used for the saved run.")
    parser.add_argument("--force-regen", type=parse_bool, required=True, help="Coherence setting used for the saved run.")
    parser.add_argument("--guided-context", type=parse_bool, required=True, help="Coherence setting used for the saved run.")
    parser.add_argument(
        "--regen-decision-count",
        type=int,
        default=0,
        help="Number of regeneration decisions made during the saved run, if known.",
    )
    return parser


def _get_vehicle_lookup_keys(vehicle_data: dict) -> list[str]:
    keys = []
    for field_name in ("name", "prompt_reference", "means_of_transport"):
        value = vehicle_data.get(field_name)
        if isinstance(value, str) and value.strip():
            keys.append(value.strip())
    return keys


def _resolve_current_vehicle(vehicle_data: dict, vehicle_registry) -> dict:
    for key in _get_vehicle_lookup_keys(vehicle_data):
        try:
            return vehicle_registry.get(key).to_dict()
        except KeyError:
            pass
        try:
            return vehicle_registry.get_by_prompt(key).to_dict()
        except KeyError:
            pass
    raise KeyError(
        "Could not match saved vehicle to current registry using "
        f"name/prompt_reference: {vehicle_data!r}"
    )


def _rebind_vehicles_to_current_registry(raw_agents: list[dict], vehicle_registry) -> list[dict]:
    rebound_agents = []

    for agent_data in raw_agents:
        if isinstance(agent_data, str):
            agent_data = json.loads(agent_data)
        rebound_agent = dict(agent_data)
        rebound_agent["owned_vehicles"] = [
            _resolve_current_vehicle(vehicle_data, vehicle_registry)
            for vehicle_data in (agent_data.get("owned_vehicles") or [])
        ]

        rebound_location_changes = []
        for location_change in agent_data.get("location_changes", []) or []:
            rebound_location_change = dict(location_change)
            rebound_possible_routes = []
            for possible_route in location_change.get("possible_routes", []) or []:
                rebound_route = dict(possible_route)
                saved_vehicle = rebound_route.get("vehicle")
                if saved_vehicle is None:
                    saved_vehicle = {
                        "name": rebound_route.get("means_of_transport"),
                        "prompt_reference": rebound_route.get("means_of_transport"),
                    }
                rebound_route["vehicle"] = _resolve_current_vehicle(saved_vehicle, vehicle_registry)
                rebound_possible_routes.append(rebound_route)
            rebound_location_change["possible_routes"] = rebound_possible_routes
            rebound_location_changes.append(rebound_location_change)

        rebound_agent["location_changes"] = rebound_location_changes
        rebound_agents.append(rebound_agent)

    return rebound_agents


def _load_json_records(path: Path) -> list[dict]:
    raw_records = json.loads(path.read_text(encoding="utf-8"))
    return [json.loads(record) if isinstance(record, str) else record for record in raw_records]


def _count_air_taxi_route_suggestions(agents) -> int:
    return sum(
        1
        for agent in agents
        for location_change in (agent.location_changes or [])
        if any(route.vehicle.name == "air_taxi" for route in (location_change.possible_routes or []))
    )


def _count_air_taxi_choices(agents) -> int:
    return sum(
        1
        for agent in agents
        for location_change in (agent.location_changes or [])
        if (location_change.decision or {}).get("vehicle_name") == "air_taxi"
    )


def _infer_day_index(output_dir: Path) -> int:
    match = re.search(r"day(\d+)", output_dir.name, re.IGNORECASE)
    return int(match.group(1)) if match else 1


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    agents4_input = Path(args.agents4_input).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else agents4_input.parent

    if not agents4_input.exists():
        raise FileNotFoundError(f"agents_4 input not found: {agents4_input}")

    from config.vehicle_config import vehicle_configs
    from model.agent import Agent
    from model.vehicle import Vehicle, VehicleRegistry
    from module.planning.day_metrics import build_rows, write_csv
    from module.planning.planning_module import PlanningModule
    from util.trips import generate_trips_xml

    if not hasattr(Vehicle, "from_json"):
        Vehicle.from_json = Vehicle.from_dict

    vehicle_registry = VehicleRegistry.from_configs(vehicle_configs)
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_agents = _load_json_records(agents4_input)
    rebound_agents = _rebind_vehicles_to_current_registry(raw_agents, vehicle_registry)
    agents = [Agent.from_json(agent_data) for agent_data in rebound_agents]

    route_descriptions = [
        {"agent_id": agent.id, **route_description}
        for agent in agents
        for route_description in (agent.route_descriptions or [])
    ]
    trips_xml, uam_trips_xml = generate_trips_xml(route_descriptions)
    (output_dir / "trips.xml").write_text(trips_xml, encoding="utf-8")
    (output_dir / "uam_trips.xml").write_text(uam_trips_xml, encoding="utf-8")

    run_info = {
        "run_name": output_dir.name,
        "model": args.model,
        "ablation": args.ablation,
        "iterative": args.iterative,
        "force_regen": args.force_regen,
        "guided_context": args.guided_context,
        "regen_decision_count": args.regen_decision_count,
        "air_taxi_suggested_count": _count_air_taxi_route_suggestions(agents),
        "air_taxi_chosen_count": _count_air_taxi_choices(agents),
    }

    inconsistent_agents = PlanningModule.check_inconsistencies(
        agents,
        vehicle_registry,
        run_info=run_info,
        csv_path=output_dir / "inconsistencies.csv",
        run_summary_csv_path=output_dir / "run_metrics.csv",
        reasoning_csv_path=output_dir / "agent_reasoning.csv",
    )

    description_path = output_dir / "agents_1_description.json"
    if description_path.exists():
        start_of_day_agents = [
            Agent.from_json(agent_data)
            for agent_data in _load_json_records(description_path)
        ]
        write_csv(
            build_rows(
                start_of_day_agents=start_of_day_agents,
                agents=agents,
                vehicle_registry=vehicle_registry,
                daily_context=None,
                day_index=_infer_day_index(output_dir),
            ),
            output_dir / "day_metrics.csv",
        )

    created_route_description_count = sum(len(agent.route_descriptions or []) for agent in agents)
    total_route_descriptions_count = sum(
        sum(
            1
            for index in range(len(agent.day_schedule.task_list) - 1)
            if agent.day_schedule.task_list[index].building_type
            != agent.day_schedule.task_list[index + 1].building_type
        )
        for agent in agents
        if agent.day_schedule and agent.day_schedule.task_list
    )

    summary = {
        "agents4_input": str(agents4_input),
        "output_dir": str(output_dir),
        "coherence_settings": {
            "model": args.model,
            "ablation": args.ablation,
            "iterative": args.iterative,
            "force_regen": args.force_regen,
            "guided_context": args.guided_context,
        },
        "total_agents": len(agents),
        "created_route_descriptions": created_route_description_count,
        "expected_route_descriptions": total_route_descriptions_count,
        "inconsistent_agent_count": len(inconsistent_agents),
        "inconsistent_agent_ids": [agent.id for agent in inconsistent_agents],
        "regen_decision_count": args.regen_decision_count,
    }

    (output_dir / "run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
