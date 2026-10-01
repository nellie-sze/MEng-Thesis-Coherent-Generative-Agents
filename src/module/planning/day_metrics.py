import csv
from pathlib import Path

from model.agent import Agent
from model.vehicle import VehicleRegistry
from model.rule import LifecycleActivationState, get_lifecycle


DAY_METRICS_HEADERS = [
    "day_index",
    "metric_type",
    "metric_name",
    "value",
]


def _chosen_vehicles(agent: Agent) -> set[str]:
    chosen_vehicle_names: set[str] = set()
    for location_change in agent.location_changes or []:
        if not location_change.decision:
            continue
        vehicle_name = location_change.decision.get("vehicle_name")
        if vehicle_name:
            chosen_vehicle_names.add(vehicle_name)
    return chosen_vehicle_names


def build_rows(
    start_of_day_agents,
    agents,
    vehicle_registry: VehicleRegistry,
    daily_context,
    day_index: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    start_of_day_agents_by_id = {agent.id: agent for agent in start_of_day_agents}

    automatic_vehicles = [
        vehicle
        for vehicle in vehicle_registry.vehicles
        if (
            get_lifecycle(vehicle) is not None
            and get_lifecycle(vehicle).activation == LifecycleActivationState.AUTOMATIC
            and get_lifecycle(vehicle).linked_attribute is not None
        )
    ]

    for vehicle in automatic_vehicles:
        lifecycle = get_lifecycle(vehicle)
        purchased_count = 0
        starting_active_count = 0

        for start_of_day_agent in start_of_day_agents:
            start_of_day_value = getattr(start_of_day_agent, lifecycle.linked_attribute, None)
            if start_of_day_value == lifecycle.linked_value:
                starting_active_count += 1

        for agent in agents:
            start_of_day_agent = start_of_day_agents_by_id.get(agent.id)
            if start_of_day_agent is None:
                continue
            start_of_day_value = getattr(start_of_day_agent, lifecycle.linked_attribute, None)
            if start_of_day_value == lifecycle.linked_value:
                continue
            if vehicle.name in _chosen_vehicles(agent):
                purchased_count += 1
        rows.append({
            "day_index": day_index,
            "metric_type": "mot_purchase",
            "metric_name": vehicle.name,
            "value": purchased_count,
        })
        rows.append({
            "day_index": day_index,
            "metric_type": "mot_existing_owners",
            "metric_name": vehicle.name,
            "value": starting_active_count,
        })

    for event in (daily_context.events if daily_context else []):
        attendance_count = 0
        for agent in agents:
            tasks = agent.day_schedule.task_list if agent.day_schedule and agent.day_schedule.task_list else []
            if any(task.building_type == event.name for task in tasks):
                attendance_count += 1
        rows.append({
            "day_index": day_index,
            "metric_type": "event_attendance",
            "metric_name": event.name,
            "value": attendance_count,
        })

    return rows


def write_csv(rows: list[dict[str, object]], csv_path: str | Path) -> None:
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DAY_METRICS_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
