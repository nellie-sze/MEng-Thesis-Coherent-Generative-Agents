from __future__ import annotations

from pathlib import Path

"""
python scenarios/run_pipeline.py --scenario NAME

"""

SCENARIOS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCENARIOS_ROOT.parent
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "results"


PIPELINE_SCENARIOS = {
    "9_euro_ticket": {
        "name": "9_euro_ticket",
        "mode": "per_day",
        "sim_config_name": "config_wedding_sumo",
        "day_config_path": "scenarios/day_configs/scenario1.py",
        "output_root": DEFAULT_OUTPUT_ROOT / "9_euro_ticket",
        "dua": {
            "last_step": 6,
            "routefile_mode": "routesonly",
            "output_last_route": True,
            "include_taxi_fleet_in_dua": False,
        },
        "sumo": {
            "nogui": True,
            "begin": 0,
            "end": 86400,
            "step_length": 1,
        },
    },

    "UAM_scenario": {
        "name": "UAM_scenario",
        "mode": "per_day",
        "sim_config_name": "config_wedding_sumo_UAM",
        "day_config_path": "scenarios/day_configs/scenario2.py",
        "output_root": DEFAULT_OUTPUT_ROOT / "UAM_scenario",
        #"preloaded_agents_path": "results/9_euro_ticket/gta/day1/agents_1_description.json",
        "uam": {
            "enabled": True,
            "build": True,
            "hub_coordinates_path": "scenarios/uam/uam_hub_coords.json",
            "output_dir": REPO_ROOT / "data" / "open_street_map" / "UAM_scenario_pipeline",
            "output_scenario_name": "UAM_scenario_pipeline",
        },
        "dua": {
            "last_step": 6,
            "routefile_mode": "routesonly",
            "output_last_route": True,
            "include_taxi_fleet_in_dua": True,
        },
        "sumo": {
            "nogui": True,
            "begin": 0,
            "end": 86400,
            "step_length": 1,
        },
    },
    "city_event": {
        "name": "city_event",
        "mode": "per_day",
        "sim_config_name": "config_wedding_sumo",
        "day_config_path": "scenarios/day_configs/scenario3.py",
        "output_root": DEFAULT_OUTPUT_ROOT / "city_event",
        "preloaded_agents_path": "results/9_euro_ticket/gta/day1/agents_1_description.json",
        "dua": {
            "last_step": 6,
            "routefile_mode": "routesonly",
            "output_last_route": True,
            "include_taxi_fleet_in_dua": False,
        },
        "sumo": {
            "nogui": True,
            "begin": 0,
            "end": 86400,
            "step_length": 1,
        },
    },
    "baseline": {
        "name": "baseline",
        "mode": "per_day",
        "sim_config_name": "config_wedding_sumo",
        "day_config_path": "src/config/daily_config.py",
        "output_root": DEFAULT_OUTPUT_ROOT / "baseline",
        "preloaded_agents_path": "results/baseline/gta/day1/agents_1_description.json",
        "dua": {
            "last_step": 6,
            "routefile_mode": "routesonly",
            "output_last_route": True,
            "include_taxi_fleet_in_dua": False,
        },
        "sumo": {
            "nogui": True,
            "begin": 0,
            "end": 86400,
            "step_length": 1,
        },
    },
}
