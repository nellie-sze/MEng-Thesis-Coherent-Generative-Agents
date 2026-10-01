from __future__ import annotations

from pathlib import Path

from model.agent import Agent
from module.planning.planning_module import PlanningModule
from multi_day_runner import _setup_day, prepare_next_day, run_day_from_state
from traffic_simulacra import ROUTES_POSTFIX
from util.storage import Storage, write_day_descriptions
from util.trips import generate_trips_xml


def _write_route_outputs(day_storage_path: Path, agents: list[Agent], net_file: str) -> None:
    storage = Storage(str(day_storage_path))
    write_day_descriptions(day_storage_path, agents, ROUTES_POSTFIX)
    trips_xml, uam_trips_xml = generate_trips_xml(
        [
            {"agent_id": agent.id, **route_description}
            for agent in agents
            for route_description in (agent.route_descriptions or [])
        ],
        net_file,
    )
    storage.write_trips(trips_xml)
    storage.write_uam_trips(uam_trips_xml)


def replay_days(state: dict, resume_day: int) -> None:
    if resume_day <= 1:
        return
    for day_index in range(1, resume_day):
        prepare_next_day(state, day_index)


def rerun_routes(state: dict, day_index: int) -> list[Agent]:
    day_storage_path, daily_context, vehicle_registry, _ = _setup_day(state, day_index, prefix="Resuming")
    stage3_input = day_storage_path / "agents_3_location_changes.json"
    if not stage3_input.exists():
        raise FileNotFoundError(f"Cannot rerun route generation: missing {stage3_input}")
    agents = Storage(str(stage3_input.parent)).get_agents(str(stage3_input))
    routed_agents = PlanningModule.add_routes_multithreaded(
        agents,
        state["config"]["workers"],
        state["config"],
        daily_context=daily_context,
        vehicle_registry=vehicle_registry,
    )
    _write_route_outputs(day_storage_path, routed_agents, state["config"]["net_file"])
    return routed_agents


def resume_day(state: dict, day_index: int, stage: str = "llm") -> list[Agent]:
    replay_days(state, day_index)
    stage_handlers = {
        "llm": lambda: run_day_from_state(state, day_index),
        "route_generation": lambda: rerun_routes(state, day_index),
        "postprocess": lambda: [],
    }
    try:
        return stage_handlers[stage]()
    except KeyError as exc:
        raise NotImplementedError(f"Unsupported resume stage {stage!r}") from exc


def continue_multi_day_from_state(
    state: dict,
    start_day: int,
    start_stage: str = "llm",
) -> list[list[Agent]]:
    if start_day < 1 or start_day > len(state["day_labels"]):
        raise ValueError(f"start_day must be between 1 and {len(state['day_labels'])}, got {start_day}")

    all_agents: list[list[Agent]] = []
    for day_index in range(start_day, len(state["day_labels"]) + 1):
        if day_index == start_day:
            final_agents = resume_day(state, day_index, stage=start_stage)
        else:
            final_agents = run_day_from_state(state, day_index)
        all_agents.append(final_agents)
        if start_stage != "postprocess":
            prepare_next_day(state, day_index)
    return all_agents
