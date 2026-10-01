from __future__ import annotations

from pathlib import Path

from config.sim_config import config_wedding_sumo as default_config
from config.vehicle_config import vehicle_configs
from model.agent import Agent
from model.daily_context import load_day_config
from model.vehicle import VehicleRegistry
from model.vehicle import build_day_registry
from module.planning.extract_journey_outcomes import extract_journey_outcomes
from traffic_simulacra import DESCRIPTION_POSTFIX, ROUTES_POSTFIX, run as run_single_day
from util.file import create_folders
from util.logging import log_info
from util.storage import Storage, existing_agents_path, prepare_day_directories, write_day_descriptions

WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
DEFAULT_DAY_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "daily_config.py"

def _day_sequence(start_day: str, num_days: int) -> list[str]:
    if num_days < 1:
        raise ValueError("num_days must be at least 1")
    if start_day not in WEEKDAYS:
        raise ValueError(f"Unsupported start day: {start_day!r}")

    start_index = WEEKDAYS.index(start_day)
    return [WEEKDAYS[(start_index + offset) % len(WEEKDAYS)] for offset in range(num_days)]


def _day_path(state: dict, day_index: int) -> Path:
    return state["parent_storage_path"] / f"day{day_index}"


def _carry_agents(
    agents: list[Agent],
    preserve_journey_outcomes: bool = False,
) -> list[Agent]:
    # Carry long-lived agent state into the next day while clearing day-specific outputs.
    carried_agents = []
    for agent in agents:
        carried_agent = Agent.from_json(agent.to_dict())
        carried_agent.reset_day_state(preserve_journey_outcomes=preserve_journey_outcomes)
        carried_agents.append(carried_agent)
    return carried_agents


def prepare_simulation(
    config: dict | None = None,
    day_config_path: str | Path = DEFAULT_DAY_CONFIG_PATH,
    initial_preloaded_agents_path: str | Path | None = None,
    preserve_existing_days: bool = False,
) -> dict:
    # Build the reusable state object once so batch and per-day flows share the same setup.
    active_config = dict(config or default_config)
    parent_storage_path = Path(active_config["storage_path"])
    create_folders(str(parent_storage_path))

    validated_day_config = load_day_config(day_config_path, run_config=active_config)

    if not preserve_existing_days:
        prepare_day_directories(
            parent_storage_path,
            DESCRIPTION_POSTFIX,
            preserve_day1_descriptions=not validated_day_config.regenerate_descriptions,
        )
    preload_agents_path = None
    if initial_preloaded_agents_path is not None:
        preload_agents_path = str(initial_preloaded_agents_path)
    elif not validated_day_config.regenerate_descriptions:
        preload_agents_path = existing_agents_path(parent_storage_path / "day1", DESCRIPTION_POSTFIX)
        if not preload_agents_path:
            log_info("REGENERATE_DESCRIPTIONS is False but no existing day 1 descriptions were found; regenerating from scratch.")

    return {
        "config": active_config,
        "day_config_path": day_config_path,
        "parent_storage_path": parent_storage_path,
        "validated_day_config": validated_day_config,
        "full_vehicle_registry": VehicleRegistry.from_configs(vehicle_configs),
        "day_labels": _day_sequence(validated_day_config.start_day, validated_day_config.num_days),
        "preload_agents_path": preload_agents_path,
        "previous_day_schedules": None,
    }


def _extract_day_outcomes(
    state: dict,
    day_index: int,
    tripinfo_path: str | Path,
    personinfo_path: str | Path,
) -> Path:
    day_storage_path = _day_path(state, day_index)
    agents_path = day_storage_path / f"agents_{ROUTES_POSTFIX}.json"
    if not agents_path.exists():
        raise FileNotFoundError(f"Cannot extract journey outcomes: missing routed agents file at {agents_path}")

    extract_journey_outcomes(
        agents_path=agents_path,
        tripinfo_path=Path(tripinfo_path),
        personinfo_path=Path(personinfo_path),
        config=state["config"],
    )
    return day_storage_path / "agents_5_journey_outcomes.json"


def _load_outcomes_by_agent(journey_outcomes_path: str | Path) -> dict[int, list]:
    agents = Storage(str(Path(journey_outcomes_path).parent)).get_agents(str(journey_outcomes_path))
    return {
        agent.id: list(agent.journey_outcomes or [])
        for agent in agents
        if getattr(agent, "journey_outcomes", None)
    }


def _load_day_agents(state: dict, day_index: int) -> list[Agent]:
    day_storage_path = _day_path(state, day_index)
    stage_paths = [
        day_storage_path / "agents_2_no_day_schedule.json",
        day_storage_path / "agents_3_no_location_changes.json",
        day_storage_path / f"agents_{ROUTES_POSTFIX}.json",
    ]
    if not all(path.exists() for path in stage_paths):
        missing = [str(path) for path in stage_paths if not path.exists()]
        raise FileNotFoundError(
            f"Cannot prepare day {day_index + 1}: missing carry-over source files for day {day_index}: {missing}"
        )

    storage = Storage(str(day_storage_path))
    merged_agents: dict[int, Agent] = {}
    for path in stage_paths:
        for agent in storage.get_agents(str(path)):
            merged_agents[agent.id] = agent
    return [merged_agents[agent_id] for agent_id in sorted(merged_agents)]


def prepare_next_day(
    state: dict,
    day_index: int,
    tripinfo_path: str | Path | None = None,
    personinfo_path: str | Path | None = None,
    journey_outcomes_path: str | Path | None = None,
) -> None:
    # Snapshot the completed day so the next day can preload agents and previous schedules.
    final_agents = _load_day_agents(state, day_index)
    resolved_journey_outcomes_path = journey_outcomes_path
    if resolved_journey_outcomes_path is None and tripinfo_path is not None and personinfo_path is not None:
        resolved_journey_outcomes_path = _extract_day_outcomes(
            state,
            day_index,
            tripinfo_path,
            personinfo_path,
        )
    if resolved_journey_outcomes_path is None:
        default_journey_outcomes_path = _day_path(state, day_index) / "agents_5_journey_outcomes.json"
        if default_journey_outcomes_path.exists():
            resolved_journey_outcomes_path = default_journey_outcomes_path
    if day_index >= len(state["day_labels"]):
        state["preload_agents_path"] = None
        return
    state["previous_day_schedules"] = {
        agent.id: agent.day_schedule
        for agent in final_agents
        if getattr(agent, "day_schedule", None) is not None
    }
    next_day_storage_path = _day_path(state, day_index + 1)
    carried_agents = _carry_agents(final_agents)
    if resolved_journey_outcomes_path is not None:
        journey_outcomes_by_id = _load_outcomes_by_agent(resolved_journey_outcomes_path)
        for carried_agent in carried_agents:
            carried_agent.journey_outcomes = journey_outcomes_by_id.get(carried_agent.id)
    state["preload_agents_path"] = write_day_descriptions(next_day_storage_path, carried_agents, DESCRIPTION_POSTFIX)


def _build_day_context(state: dict, day_index: int):
    day_label = state["day_labels"][day_index - 1]
    daily_context = state["validated_day_config"].daily_contexts[day_index - 1].with_day(day_label)
    vehicle_registry = build_day_registry(
        vehicle_configs,
        validation_registry=state["full_vehicle_registry"],
        active_vehicle_names=list(daily_context.activate or []),
        inactive_vehicle_names=list(daily_context.deactivate or []),
        context_label=daily_context.day or "unknown day",
    )
    return day_label, daily_context, vehicle_registry

def _setup_day(state: dict, day_index: int, prefix: str = "Starting") -> tuple[Path, object, VehicleRegistry, str | None]:
    day_label, daily_context, vehicle_registry = _build_day_context(state, day_index)
    day_storage_path = _day_path(state, day_index)
    preload_agents_path = state["preload_agents_path"]
    if preload_agents_path and not Path(preload_agents_path).exists():
        if day_index == 1 and not state["validated_day_config"].regenerate_descriptions:
            log_info("REGENERATE_DESCRIPTIONS is False but no existing day 1 descriptions were found; regenerating from scratch.")
        else:
            log_info(f"Preloaded agents file {preload_agents_path} was not found for day {day_index}; regenerating from scratch.")
        preload_agents_path = None
        state["preload_agents_path"] = None
    log_info("\n\n")
    log_info(f"{prefix} day {day_index} ({day_label}) in {day_storage_path}")
    log_info(
        f"Using {len(vehicle_registry.vehicles)} active vehicles for {day_label}: "
        f"{[vehicle.name for vehicle in vehicle_registry.vehicles]}\n"
    )
    return day_storage_path, daily_context, vehicle_registry, preload_agents_path


def run_day_from_state(state: dict, day_index: int) -> list[Agent]:
    # Execute exactly one simulation day; the pipeline uses this to alternate LLM and SUMO runs.
    day_storage_path, daily_context, vehicle_registry, preload_agents_path = _setup_day(state, day_index)
    return run_single_day(
        storage_path_override=str(day_storage_path),
        preloaded_agents_path=preload_agents_path,
        day_index=day_index,
        config=state["config"],
        daily_context=daily_context,
        vehicle_registry=vehicle_registry,
        full_vehicle_registry=state["full_vehicle_registry"],
        previous_day_schedules=state["previous_day_schedules"],
    )

def run_multi_day(
    config: dict | None = None,
    day_config_path: str | Path = DEFAULT_DAY_CONFIG_PATH,
    initial_preloaded_agents_path: str | Path | None = None,
) -> list[list[Agent]]:
    # Original batch flow: run every day back-to-back using the shared state helpers above.
    state = prepare_simulation(
        config=config,
        day_config_path=day_config_path,
        initial_preloaded_agents_path=initial_preloaded_agents_path,
    )
    all_agents: list[list[Agent]] = []
    for day_index in range(1, len(state["day_labels"]) + 1):
        final_agents = run_day_from_state(state, day_index)
        all_agents.append(final_agents)
        prepare_next_day(state, day_index)
    return all_agents


def main() -> None:
    run_multi_day(config=default_config, day_config_path=DEFAULT_DAY_CONFIG_PATH)

if __name__ == "__main__":
    main()
