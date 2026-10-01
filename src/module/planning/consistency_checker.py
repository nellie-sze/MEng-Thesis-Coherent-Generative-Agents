import csv
import json
from pathlib import Path
from typing import Any, List

from config.coherence_config import config
from model.agent import Agent
from model.rule import LegContext, build_leg_contexts
from model.vehicle import VehicleRegistry
from util.logging import log_debug
from util.time import time_to_seconds


print_inconsistencies = config["print_inconsistencies"]

CSV_HEADERS = [
    "run_name",
    "model",
    "ablation",
    "iterative",
    "force_regen",
    "guided_context",
    "agent_id",
    "route_id",
    "leg_index",
    "inconsistency_index",
    "trigger_vehicle",
    "trigger_rule",
    "inconsistency_string",
    "from_polygon_id",
    "to_polygon_id",
    "from_x",
    "from_y",
    "to_x",
    "to_y",
    "departure_time",
    "arrival_time",
    "chosen_mode",
    "possible_modes",
    "distance_km",
    "travel_time_m",
]

RUN_SUMMARY_HEADERS = [
    "run_name",
    "model",
    "ablation",
    "iterative",
    "force_regen",
    "guided_context",
    "regen_decision_count",
    "air_taxi_suggested_count",
    "air_taxi_chosen_count",
    "total_agents",
    "inconsistent_agent_count",
    "invalid_leg_count",
    "expected_route_count",
    "generated_route_count",
    "inconsistency_rate_per_route",
    "completion_rate",
]

REASONING_CSV_HEADERS = [
    "agent_id",
    "route_id",
    "leg_index",
    "from_task",
    "to_task",
    "mot_choice",
    "distance_km",
    "travel_time_m",
    "reasoning",
    "inconsistencies",
]


def build_row(leg: LegContext, run_info: dict[str, Any], trigger_vehicle: str | None, trigger_rule: str,
              inconsistency_string: str) -> dict[str, Any]:
    return {
        "run_name": run_info["run_name"],
        "model": run_info["model"],
        "ablation": run_info["ablation"],
        "iterative": run_info["iterative"],
        "force_regen": run_info["force_regen"],
        "guided_context": run_info["guided_context"],
        "agent_id": leg.agent_id,
        "route_id": leg.route_id,
        "leg_index": leg.leg_index,
        "inconsistency_index": None,
        "trigger_vehicle": trigger_vehicle,
        "trigger_rule": trigger_rule,
        "inconsistency_string": inconsistency_string,
        "from_polygon_id": leg.from_polygon_id,
        "to_polygon_id": leg.to_polygon_id,
        "from_x": leg.from_x,
        "from_y": leg.from_y,
        "to_x": leg.to_x,
        "to_y": leg.to_y,
        "departure_time": leg.departure_time,
        "arrival_time": leg.arrival_time,
        "chosen_mode": leg.vehicle_choice,
        "possible_modes": leg.possible_modes,
        "distance_km": leg.distance_km,
        "travel_time_m": leg.duration_minutes,
    }


def assign_inconsistency_indices(agent_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(agent_rows, key=lambda row: (row["leg_index"], row["route_id"], row["trigger_rule"]))
    for index, row in enumerate(ordered):
        row["inconsistency_index"] = index
    return ordered


def build_route_availability_rows(context: List[LegContext], run_info: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for leg in context:
        if leg.possible_mot and leg.vehicle_choice not in leg.possible_mot:
            rows.append(build_row(
                leg=leg,
                run_info=run_info,
                trigger_vehicle=None,
                trigger_rule="route_availability",
                inconsistency_string=f"Chose {leg.vehicle_choice} which was not among the possible means of transport: {leg.possible_mot}",
            ))
    return rows


def build_home_rows(agent: Agent, context: List[LegContext], run_info: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    if not context or not agent.home:
        return rows

    first_leg = context[0]
    last_leg = context[-1]

    if first_leg.from_polygon_id != agent.home.polygon_id:
        rows.append(build_row(
            leg=first_leg,
            run_info=run_info,
            trigger_vehicle=None,
            trigger_rule="home_consistency_leave",
            inconsistency_string="Does not leave from home",
        ))

    if last_leg.to_polygon_id != agent.home.polygon_id:
        rows.append(build_row(
            leg=last_leg,
            run_info=run_info,
            trigger_vehicle=None,
            trigger_rule="home_consistency_return",
            inconsistency_string="Does not return to home",
        ))

    return rows


def _tasks_match(left_task, right_task) -> bool:
    if left_task is right_task:
        return True
    if left_task is None or right_task is None:
        return False
    return (
        left_task.time == right_task.time
        and left_task.action == right_task.action
        and left_task.building_type == right_task.building_type
    )


def _find_leg_for_event_task(agent: Agent, context: List[LegContext], event_task):
    if not event_task:
        return context[0] if context else None

    location_changes = agent.location_changes or []
    for leg, location_change in zip(context, location_changes):
        if _tasks_match(location_change.to_task, event_task):
            return leg
    for leg, location_change in zip(context, location_changes):
        if _tasks_match(location_change.from_task, event_task):
            return leg
    return context[0] if context else None


def build_event_rows(agent: Agent, context: List[LegContext], run_info: dict[str, Any], daily_context=None) -> list[dict[str, Any]]:
    rows = []
    if daily_context is None or not getattr(daily_context, "events", None):
        return rows
    if not agent.day_schedule or not agent.day_schedule.task_list:
        return rows

    for event in daily_context.events:
        event_start_seconds = time_to_seconds(event.start_time)
        event_end_seconds = time_to_seconds(event.end_time)
        if event_start_seconds is None or event_end_seconds is None:
            continue

        earliest_allowed = event_start_seconds - 3600
        latest_allowed = event_end_seconds + 3600

        for task in agent.day_schedule.task_list:
            if task.building_type != event.name:
                continue
            task_time_seconds = time_to_seconds(task.time)
            if task_time_seconds is None:
                continue
            if earliest_allowed <= task_time_seconds <= latest_allowed:
                continue

            related_leg = _find_leg_for_event_task(agent, context, task)
            if related_leg is None:
                continue

            rows.append(build_row(
                leg=related_leg,
                run_info=run_info,
                trigger_vehicle=None,
                trigger_rule="event_time",
                inconsistency_string=f"Attended {event.name} outside of event time",
            ))

    return rows


def build_vehicle_rows(context: List[LegContext], agent: Agent, vehicle,
                       run_info: dict[str, Any], vehicle_registry: VehicleRegistry) -> list[dict[str, Any]]:
    rows = []
    for rule in vehicle.rules:
        for leg, message in rule.evaluate_all(context, agent, vehicle.name, vehicle_registry):
            rows.append(build_row(
                leg=leg,
                run_info=run_info,
                trigger_vehicle=vehicle.name,
                trigger_rule=rule.category,
                inconsistency_string=message,
            ))
    return rows


def build_agent_rows(agent: Agent, vehicle_registry: VehicleRegistry,
                     run_info: dict[str, Any], daily_context=None) -> list[dict[str, Any]]:
    context = build_leg_contexts(agent)
    rows = []
    rows.extend(build_route_availability_rows(context, run_info))
    rows.extend(build_home_rows(agent, context, run_info))
    rows.extend(build_event_rows(agent, context, run_info, daily_context))

    for vehicle in vehicle_registry.vehicles:
        rows.extend(build_vehicle_rows(context, agent, vehicle, run_info, vehicle_registry))

    if rows and print_inconsistencies:
        messages = [row["inconsistency_string"] for row in sorted(rows, key=lambda item: item["leg_index"])]
        log_debug(f"Agent {agent.id} inconsistencies:\n" + "\n".join(messages))

    return assign_inconsistency_indices(rows)


def build_inconsistency_rows(agents: List[Agent], vehicle_registry: VehicleRegistry,
                             run_info: dict[str, Any], daily_context=None) -> list[dict[str, Any]]:
    all_rows = []
    for agent in agents:
        all_rows.extend(build_agent_rows(agent, vehicle_registry, run_info, daily_context))
    return all_rows


def _write_csv(csv_path: str | Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_inconsistency_csv(rows: list[dict[str, Any]], csv_path: str | Path) -> None:
    _write_csv(csv_path, CSV_HEADERS, rows)


def count_expected_routes(agents: List[Agent]) -> int:
    return sum(
        sum(
            1
            for index in range(len(agent.day_schedule.task_list) - 1)
            if agent.day_schedule.task_list[index].building_type != agent.day_schedule.task_list[index + 1].building_type
        )
        for agent in agents
        if agent.day_schedule and agent.day_schedule.task_list
    )


def count_generated_routes(agents: List[Agent]) -> int:
    return sum(len(agent.route_descriptions or []) for agent in agents)


def build_run_summary_row(agents: List[Agent], rows: list[dict[str, Any]], run_info: dict[str, Any]) -> dict[str, Any]:
    total_agents = len(agents)
    inconsistent_agent_count = len({row["agent_id"] for row in rows})
    invalid_leg_count = len(rows)
    expected_route_count = count_expected_routes(agents)
    generated_route_count = count_generated_routes(agents)

    inconsistency_rate_per_route = (
        invalid_leg_count / generated_route_count if generated_route_count > 0 else None
    )
    completion_rate = (
        generated_route_count / expected_route_count if expected_route_count > 0 else None
    )

    return {
        "run_name": run_info["run_name"],
        "model": run_info["model"],
        "ablation": run_info["ablation"],
        "iterative": run_info["iterative"],
        "force_regen": run_info["force_regen"],
        "guided_context": run_info["guided_context"],
        "regen_decision_count": run_info.get("regen_decision_count", 0),
        "air_taxi_suggested_count": run_info.get("air_taxi_suggested_count", 0),
        "air_taxi_chosen_count": run_info.get("air_taxi_chosen_count", 0),
        "total_agents": total_agents,
        "inconsistent_agent_count": inconsistent_agent_count,
        "invalid_leg_count": invalid_leg_count,
        "expected_route_count": expected_route_count,
        "generated_route_count": generated_route_count,
        "inconsistency_rate_per_route": inconsistency_rate_per_route,
        "completion_rate": completion_rate,
    }


def write_run_summary_csv(summary_row: dict[str, Any], csv_path: str | Path) -> None:
    _write_csv(csv_path, RUN_SUMMARY_HEADERS, [summary_row])


def build_reasoning_rows(agents: List[Agent], inconsistency_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inconsistencies_by_leg: dict[tuple[Any, Any], list[str]] = {}
    for row in inconsistency_rows:
        key = (row["agent_id"], row["route_id"])
        inconsistencies_by_leg.setdefault(key, []).append(row["inconsistency_string"])

    reasoning_rows = []
    for agent in agents:
        leg_contexts_by_route_id = {
            str(leg.route_id): leg
            for leg in build_leg_contexts(agent)
        }
        for leg_index, location_change in enumerate(agent.location_changes or []):
            decision = location_change.decision or {}
            route_id = str(location_change.route_id)
            key = (agent.id, route_id)
            leg_context = leg_contexts_by_route_id.get(route_id)
            reasoning_rows.append({
                "agent_id": agent.id,
                "route_id": route_id,
                "leg_index": leg_index,
                "reasoning": decision.get("reasoning", ""),
                "inconsistencies": (
                    json.dumps(inconsistencies_by_leg[key], ensure_ascii=True)
                    if key in inconsistencies_by_leg
                    else "Consistent"
                ),
                "from_task": location_change.from_task.action,
                "to_task": location_change.to_task.action,
                "mot_choice": decision.get("means_of_transport", ""),
                "distance_km": leg_context.distance_km if leg_context is not None else None,
                "travel_time_m": leg_context.duration_minutes if leg_context is not None else None,
            })

    return reasoning_rows


def write_reasoning_csv(rows: list[dict[str, Any]], csv_path: str | Path) -> None:
    _write_csv(csv_path, REASONING_CSV_HEADERS, rows)


def check_inconsistencies(agents: List[Agent], vehicle_registry: VehicleRegistry,
                          run_info: dict[str, Any] | None = None,
                          daily_context=None,
                          csv_path: str | Path | None = None,
                          run_summary_csv_path: str | Path | None = None,
                          reasoning_csv_path: str | Path | None = None) -> list[Agent]:
    if run_info is None:
        run_info = {
            "run_name": "unknown",
            "model": config["model"],
            "ablation": config["ablation"],
            "iterative": config["iterative"],
            "force_regen": config["force_regen"],
            "guided_context": config["guided_context"],
        }

    rows = build_inconsistency_rows(agents, vehicle_registry, run_info, daily_context)
    if csv_path is not None:
        write_inconsistency_csv(rows, csv_path)
    if run_summary_csv_path is not None:
        write_run_summary_csv(build_run_summary_row(agents, rows, run_info), run_summary_csv_path)
    if reasoning_csv_path is not None:
        write_reasoning_csv(build_reasoning_rows(agents, rows), reasoning_csv_path)

    inconsistent_agent_ids = {row["agent_id"] for row in rows}
    return [agent for agent in agents if agent.id in inconsistent_agent_ids]
