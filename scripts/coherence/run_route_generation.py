"""
python scripts/run_route_generation.py \
  --stage3-input results/wedding-sumo/agents_3_location_changes.json \
  --output-dir results/coherence_sweep/run_001 \
  --model Qwen/Qwen3.5-0.8B \
  --ablation false \
  --iterative true \
  --force-regen false \
  --guided-context false \
  --max-attempts 2
"""

import argparse
import importlib
import json
import os
import shutil
import sys
from copy import deepcopy
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


def load_base_config(config_name: str) -> dict:
    config_module = importlib.import_module("config.sim_config")
    try:
        return deepcopy(getattr(config_module, config_name))
    except AttributeError as exc:
        raise ValueError(f"Unknown base config {config_name!r}.") from exc


def apply_coherence_settings(args) -> dict:
    coherence_module = importlib.import_module("config.coherence_config")
    coherence_module.config.update({
        "ablation": args.ablation,
        "iterative": args.iterative,
        "force_regen": args.force_regen,
        "max_attempts": args.max_attempts,
        "guided_context": args.guided_context,
        "model": args.model,
        "print_prompts": args.print_prompts,
        "print_responses": args.print_responses,
        "print_inconsistencies": args.print_inconsistencies,
    })
    return dict(coherence_module.config)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the route-generation stage only, starting from a frozen agents_3_location_changes.json."
    )
    parser.add_argument(
        "--stage3-input",
        required=True,
        help="Path to the frozen agents_3_location_changes.json file.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where this run's outputs should be stored.",
    )
    parser.add_argument(
        "--base-config",
        default="config_wedding_sumo",
        help="Name of the base config from src/config/sim_config.py to use for SUMO/network paths.",
    )
    parser.add_argument("--ablation", type=parse_bool, required=True, help="Coherence setting.")
    parser.add_argument("--iterative", type=parse_bool, required=True, help="Coherence setting.")
    parser.add_argument("--force-regen", type=parse_bool, required=True, help="Coherence setting.")
    parser.add_argument("--max-attempts", type=int, required=True, help="Coherence setting.")
    parser.add_argument("--guided-context", type=parse_bool, default=False, help="Coherence setting.")
    parser.add_argument("--model", required=True, help="Model id forwarded through coherence_config.")
    parser.add_argument("--print-prompts", type=parse_bool, default=False, help="Debug output toggle.")
    parser.add_argument("--print-responses", type=parse_bool, default=False, help="Debug output toggle.")
    parser.add_argument("--print-inconsistencies", type=parse_bool, default=False, help="Debug output toggle.")
    parser.add_argument(
        "--gpu-id",
        type=int,
        default=0,
        help="GPU id forwarded to the route-decision LLM wrapper.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    stage3_input = Path(args.stage3_input).resolve()
    output_dir = Path(args.output_dir).resolve()

    if not stage3_input.exists():
        raise FileNotFoundError(f"Stage-3 input file not found: {stage3_input}")

    coherence_settings = apply_coherence_settings(args)
    base_config = load_base_config(args.base_config)
    base_config["storage_path"] = str(output_dir)

    from config.vehicle_config import vehicle_configs
    from model.vehicle import Vehicle, VehicleRegistry
    from module.action.closest_location_choice import ClosestLocationChoice
    from module.action.sumo.sumo_adapter import SumoAdapter
    from module.planning.planning_module import PlanningModule
    from util.logging import log_info
    from util.storage import Storage
    from util.time import Timer
    from util.trips import generate_trips_xml

    output_dir.mkdir(parents=True, exist_ok=True)

    log_info("Starting route-only worker run")
    timer = Timer()
    timer.start()

    # Storage.get_agents -> Agent.from_json currently expects Vehicle.from_json,
    # but Vehicle only exposes from_dict in this codebase.
    if not hasattr(Vehicle, "from_json"):
        Vehicle.from_json = Vehicle.from_dict

    vehicle_registry = VehicleRegistry.from_configs(vehicle_configs)

    input_storage = Storage(str(output_dir / "_tmp_loader"))
    agents = input_storage.get_agents(str(stage3_input))

    storage = Storage(str(output_dir))
    storage.write_agents([], "1_no_description")
    storage.write_agents(agents, "1_description")
    storage.write_agents(agents, "2_day_schedule")
    storage.write_agents([], "2_no_day_schedule")
    storage.write_agents(agents, "3_location_changes")
    storage.write_agents([], "3_no_location_changes")

    urban_sampler = ClosestLocationChoice(base_config["buildings_file"], base_config["taz_file"])
    traffic_sim = SumoAdapter(
        urban_sampler,
        base_config["net_file"],
        base_config["poly_file"],
        base_config["v_types_file"],
        base_config["pt_stops_file"],
        base_config["pt_vehicles_file"],
        base_config,
        vehicle_registry=vehicle_registry,
    )

    try:
        final_agents = PlanningModule.add_routes(
            agents,
            args.gpu_id,
            traffic_sim,
            vehicle_registry=vehicle_registry,
        )
        if final_agents is None:
            raise RuntimeError("Route generation failed before agents could be written.")

        storage.write_agents(final_agents, "4_route_descriptions")

        route_descriptions = [
            {"agent_id": agent.id, **route_description}
            for agent in final_agents
            for route_description in agent.route_descriptions
        ]
        trips_xml, uam_trips_xml = generate_trips_xml(route_descriptions)
        storage.write_trips(trips_xml)
        storage.write_uam_trips(uam_trips_xml)

        run_info = {
            "run_name": os.path.basename(str(output_dir)),
            "model": coherence_settings.get("model"),
            "ablation": coherence_settings.get("ablation"),
            "iterative": coherence_settings.get("iterative"),
            "force_regen": coherence_settings.get("force_regen"),
            "guided_context": coherence_settings.get("guided_context"),
            "regen_decision_count": PlanningModule.last_regen_count,
        }
        inconsistent_agents = PlanningModule.check_inconsistencies(
            final_agents,
            vehicle_registry,
            run_info=run_info,
            csv_path=output_dir / "inconsistencies.csv",
            run_summary_csv_path=output_dir / "run_metrics.csv",
            reasoning_csv_path=output_dir / "agent_reasoning.csv",
        )
        created_route_description_count = sum(len(agent.route_descriptions) for agent in final_agents)
        total_route_descriptions_count = sum(
            sum(
                1
                for index in range(len(agent.day_schedule.task_list) - 1)
                if agent.day_schedule.task_list[index].building_type
                != agent.day_schedule.task_list[index + 1].building_type
            )
            for agent in final_agents
        )

        summary = {
            "stage3_input": str(stage3_input),
            "output_dir": str(output_dir),
            "base_config": args.base_config,
            "coherence_settings": coherence_settings,
            "gpu_id": args.gpu_id,
            "total_agents": len(final_agents),
            "created_route_descriptions": created_route_description_count,
            "expected_route_descriptions": total_route_descriptions_count,
            "inconsistent_agent_count": len(inconsistent_agents),
            "inconsistent_agent_ids": [agent.id for agent in inconsistent_agents],
            "regen_decision_count": PlanningModule.last_regen_count,
            "runtime_hhmm": timer.stop(),
        }

        (output_dir / "run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        shutil.copy2(stage3_input, output_dir / "stage3_input_snapshot.json")
        log_info(f"Route-only worker finished. Summary written to {output_dir / 'run_summary.json'}")
    finally:
        traffic_sim.stop_sim()
        tmp_loader_dir = output_dir / "_tmp_loader"
        if tmp_loader_dir.exists():
            shutil.rmtree(tmp_loader_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
