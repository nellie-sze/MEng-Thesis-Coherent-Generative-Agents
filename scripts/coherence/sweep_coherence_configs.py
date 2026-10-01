""" 
source venv/Scripts/activate

python scripts/coherence/sweep_coherence_configs.py \
  --generate-base-run \
  --output-base results/coherence_sweep

python scripts/coherence/sweep_coherence_configs.py \
  --stage3-input "results/wedding-sumo/agents_3_location_changes.json" \
  --output-base "results/coherence_sweep" \
  --skip-existing
 """

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
WORKER_SCRIPT = SCRIPT_DIR / "run_route_generation.py"

MODELS = [
    "Qwen/Qwen3.5-2B",
    "Qwen/Qwen3.5-4B",
    "Qwen/Qwen3.5-9B",
]

PRESETS = [
    {
        "run_id": "00",
        "name": "vanilla",
        "ablation": True,
        "iterative": False,
        "force_regen": False,
        "guided_context": False,
        "max_attempts": 2,
    },
    {
        "run_id": "01",
        "name": "iterative_only",
        "ablation": False,
        "iterative": True,
        "force_regen": False,
        "guided_context": False,
        "max_attempts": 2,
    },
    {
        "run_id": "02",
        "name": "iterative_forced_regen",
        "ablation": False,
        "iterative": True,
        "force_regen": True,
        "guided_context": False,
        "max_attempts": 2,
    },
    {
        "run_id": "03",
        "name": "iterative_guided_context",
        "ablation": False,
        "iterative": True,
        "force_regen": False,
        "guided_context": True,
        "max_attempts": 2,
    },
    {
        "run_id": "04",
        "name": "iterative_forced_regen_guided_context",
        "ablation": False,
        "iterative": True,
        "force_regen": True,
        "guided_context": True,
        "max_attempts": 2,
    },
    {
        "run_id": "05",
        "name": "forced_regen_only",
        "ablation": False,
        "iterative": False,
        "force_regen": True,
        "guided_context": False,
        "max_attempts": 2,
    },
    {
        "run_id": "06",
        "name": "forced_regen_guided_context",
        "ablation": False,
        "iterative": False,
        "force_regen": True,
        "guided_context": True,
        "max_attempts": 2,
    },
    {
        "run_id": "07",
        "name": "guided_context_only",
        "ablation": False,
        "iterative": False,
        "force_regen": False,
        "guided_context": True,
        "max_attempts": 2,
    },
]


def parse_bool(value: bool) -> str:
    return "true" if value else "false"


def slugify_model(model_name: str) -> str:
    return model_name.split("/")[-1].lower()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sweep coherence settings and model choices by repeatedly launching the route-only worker."
    )
    parser.add_argument(
        "--stage3-input",
        default=None,
        help="Path to the frozen agents_3_location_changes.json file to reuse for every run.",
    )
    parser.add_argument(
        "--generate-base-run",
        action="store_true",
        help="Generate a fresh baseline run through stage 3 before starting the sweep.",
    )
    parser.add_argument(
        "--output-base",
        required=True,
        help="Base output directory for the whole sweep.",
    )
    parser.add_argument(
        "--base-config",
        default="config_wedding_sumo",
        help="Name of the base config from src/config/sim_config.py to use for SUMO/network paths.",
    )
    parser.add_argument(
        "--base-run-model",
        default=None,
        help="Optional model id to use for the base run that creates descriptions/day schedules.",
    )
    parser.add_argument(
        "--python-executable",
        default=sys.executable,
        help="Python interpreter used to launch the worker. Defaults to the current interpreter.",
    )
    parser.add_argument("--gpu-id", type=int, default=0, help="GPU id forwarded to each worker run.")
    parser.add_argument("--print-prompts", action="store_true", help="Enable prompt logging in worker runs.")
    parser.add_argument("--print-responses", action="store_true", help="Enable response logging in worker runs.")
    parser.add_argument(
        "--no-print-inconsistencies",
        action="store_true",
        help="Disable inconsistency logging in worker runs.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip runs whose agents_4_route_descriptions.json already exists.",
    )
    return parser


def load_base_config(config_name: str) -> dict:
    if str(PROJECT_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))

    config_module = importlib.import_module("config.sim_config")
    try:
        config = getattr(config_module, config_name)
    except AttributeError as exc:
        raise ValueError(f"Unknown base config {config_name!r}.") from exc
    return dict(config)


def generate_base_stage3_run(base_config_name: str, base_run_model: str | None) -> Path:
    if str(PROJECT_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT / "src"))

    coherence_module = importlib.import_module("config.coherence_config")
    if base_run_model is not None:
        coherence_module.config["model"] = base_run_model

    from config.vehicle_config import vehicle_configs
    from model.vehicle import VehicleRegistry
    from module.action.closest_location_choice import ClosestLocationChoice
    from module.action.sumo.sumo_adapter import SumoAdapter
    from module.planning.planning_module import PlanningModule
    from module.profile.profile_module import ProfileModule
    from module.profile.seed.mid_b1_seed_generator import SeedGeneratorMiD
    from util.logging import log_info
    from util.storage import Storage

    base_config = load_base_config(base_config_name)
    base_output_dir = (PROJECT_ROOT / base_config["storage_path"]).resolve()

    log_info(f"Generating base run through stage 3 in {base_output_dir}")
    log_info(f"Base config used:\n{base_config}")

    vehicle_registry = VehicleRegistry.from_configs(vehicle_configs)
    storage = Storage(base_config["storage_path"])
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
        seed_generator = SeedGeneratorMiD(base_config["census_paths"])
        final_agents = ProfileModule.generate_seeded_agents(
            seed_generator,
            base_config["num_agents"],
            vehicle_registry,
        )
        final_agents, agents_without_description = ProfileModule.build_profiles_batch(
            final_agents,
            base_config["workers"],
            base_config["exclude_too_young"],
            base_config["exclude_too_old"],
            vehicle_registry,
        )
        storage.write_agents(final_agents, "1_description")
        storage.write_agents(agents_without_description, "1_no_description")

        building_options = traffic_sim.get_building_categories_string()
        final_agents, agents_without_day_schedule = PlanningModule.generate_day_schedules_with_places_multithreaded(
            final_agents,
            building_options,
            base_config["workers"],
            base_config["day"],
            vehicle_registry,
        )
        storage.write_agents(final_agents, "2_day_schedule")
        storage.write_agents(agents_without_day_schedule, "2_no_day_schedule")

        final_agents, agents_without_location_changes = PlanningModule.extend_with_location_changes_multithreaded(
            final_agents,
            base_config["workers"],
            traffic_sim,
        )
        storage.write_agents(final_agents, "3_location_changes")
        storage.write_agents(agents_without_location_changes, "3_no_location_changes")
    finally:
        traffic_sim.stop_sim()

    return base_output_dir / "agents_3_location_changes.json"


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    output_base = Path(args.output_base).resolve()
    output_base.mkdir(parents=True, exist_ok=True)

    if not WORKER_SCRIPT.exists():
        raise FileNotFoundError(f"Worker script not found: {WORKER_SCRIPT}")

    if args.generate_base_run:
        stage3_input = generate_base_stage3_run(args.base_config, args.base_run_model)
    elif args.stage3_input is not None:
        stage3_input = Path(args.stage3_input).resolve()
    else:
        raise ValueError("Provide --stage3-input or use --generate-base-run.")

    if not stage3_input.exists():
        raise FileNotFoundError(f"Stage-3 input file not found: {stage3_input}")

    manifest = {
        "stage3_input": str(stage3_input),
        "generated_base_run": args.generate_base_run,
        "base_run_model": args.base_run_model,
        "base_config": args.base_config,
        "python_executable": args.python_executable,
        "gpu_id": args.gpu_id,
        "models": MODELS,
        "presets": PRESETS,
        "runs": [],
    }

    total_runs = len(MODELS) * len(PRESETS)
    current_index = 0

    for model in MODELS:
        model_slug = slugify_model(model)
        for preset in PRESETS:
            current_index += 1
            run_dir = output_base / model_slug / f"{preset['run_id']}_{preset['name']}"
            run_dir.mkdir(parents=True, exist_ok=True)

            command = [
                args.python_executable,
                str(WORKER_SCRIPT),
                "--stage3-input",
                str(stage3_input),
                "--output-dir",
                str(run_dir),
                "--base-config",
                args.base_config,
                "--model",
                model,
                "--ablation",
                parse_bool(preset["ablation"]),
                "--iterative",
                parse_bool(preset["iterative"]),
                "--force-regen",
                parse_bool(preset["force_regen"]),
                "--max-attempts",
                str(preset["max_attempts"]),
                "--guided-context",
                parse_bool(preset["guided_context"]),
                "--gpu-id",
                str(args.gpu_id),
                "--print-prompts",
                parse_bool(args.print_prompts),
                "--print-responses",
                parse_bool(args.print_responses),
                "--print-inconsistencies",
                parse_bool(not args.no_print_inconsistencies),
            ]

            run_metadata = {
                "index": current_index,
                "total_runs": total_runs,
                "model": model,
                "model_slug": model_slug,
                "preset": preset,
                "output_dir": str(run_dir),
                "command": command,
            }
            manifest["runs"].append(run_metadata)
            agents4_output = run_dir / "agents_4_route_descriptions.json"
            if args.skip_existing and agents4_output.exists():
                run_metadata["skipped"] = True
                run_metadata["skip_reason"] = f"Existing output found at {agents4_output}"
                print(f"[{current_index}/{total_runs}] Skipping {model_slug} :: {preset['run_id']} {preset['name']} (existing agents_4_route_descriptions.json)")
                manifest_path = output_base / "sweep_manifest.json"
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                continue

            print(f"[{current_index}/{total_runs}] Running {model_slug} :: {preset['run_id']} {preset['name']}")

            completed = subprocess.run(command, cwd=PROJECT_ROOT)
            if completed.returncode != 0:
                failed_manifest_path = output_base / "sweep_manifest_failed.json"
                failed_manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                raise RuntimeError(
                    f"Worker run failed for model={model} preset={preset['name']} "
                    f"with exit code {completed.returncode}. Manifest written to {failed_manifest_path}"
                )

            manifest_path = output_base / "sweep_manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    final_manifest_path = output_base / "sweep_manifest.json"
    final_manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Sweep complete. Manifest written to {final_manifest_path}")


if __name__ == "__main__":
    main()
