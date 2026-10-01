from model.agent import Agent
from model.rule import (
    FirstUseLocationRule,
    FirstUseLocationState,
    LifecycleActivationState,
    LifecycleDeactivationState,
    OwnershipRule,
    OwnershipState,
    StickyRule,
    get_lifecycle,
    get_sticky_rule,
)
from model.cost_functions import get_activation_cost_vehicles
from model.vehicle import VehicleRegistry
from config.vehicle_config import vehicle_configs


def _join_labels(labels: list[str]) -> str:
    labels = list(dict.fromkeys(labels))
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} or {labels[1]}"
    return ", ".join(labels[:-1]) + f", or {labels[-1]}"


def _join_and(labels: list[str]) -> str:
    labels = list(dict.fromkeys(labels))
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _prompt_label(vehicle_registry: VehicleRegistry, vehicle_name: str) -> str:
    try:
        vehicle = vehicle_registry.get(vehicle_name)
    except KeyError:
        fallback_vehicle_config = next(
            (vehicle_config for vehicle_config in vehicle_configs if vehicle_config["name"] == vehicle_name),
            None,
        )
        if fallback_vehicle_config is not None:
            return fallback_vehicle_config.get("prompt_reference", vehicle_name)
        return vehicle_name
    return vehicle.prompt_reference if vehicle else vehicle_name


def format_owned_vehicles(agent: Agent, vehicle_registry: VehicleRegistry) -> str:
    owned_vehicle_labels = [vehicle.prompt_reference for vehicle in agent.owned_vehicles]
    if not owned_vehicle_labels:
        ownable_vehicle_labels = [
            vehicle.prompt_reference for vehicle in vehicle_registry.ownable
        ]
        if ownable_vehicle_labels:
            return f"You do not own {_join_labels(ownable_vehicle_labels)}.\n"
        return "You do not own any personal vehicles.\n"
    if len(owned_vehicle_labels) == 1:
        return f"You own a {owned_vehicle_labels[0]}.\n"
    return f"You own {_join_and(owned_vehicle_labels)}.\n"


def _format_sticky_exception(vehicle_name: str, exception, vehicle_registry: VehicleRegistry) -> str:
    allowed_vehicle_names = exception.data.get("allowed_vehicles", [])
    allowed_vehicle_labels = [
        _prompt_label(vehicle_registry, allowed_vehicle_name)
        for allowed_vehicle_name in allowed_vehicle_names
    ]

    threshold_parts = []
    distance_threshold = exception.data.get("distance_threshold")
    duration_threshold = exception.data.get("duration_threshold")
    if distance_threshold is not None:
        threshold_parts.append(f"under {distance_threshold} km")
    if duration_threshold is not None:
        threshold_parts.append(f"under {duration_threshold} minutes")

    threshold_text = ""
    if threshold_parts:
        threshold_text = " for routes of " + " and ".join(threshold_parts)

    return (
        f"You may temporarily switch away from {vehicle_name} to "
        f"{_join_labels(allowed_vehicle_labels)} instead{threshold_text}."
    )


def _home_vehicles(agent: Agent) -> list:
    return [
        vehicle
        for vehicle in agent.owned_vehicles
        if any(
            isinstance(rule, FirstUseLocationRule) and rule.state == FirstUseLocationState.FROM_HOME
            for rule in vehicle.rules
        )
    ]


def _sticky_vehicles(active_vehicle_names: set[str], vehicle_registry: VehicleRegistry) -> list:
    active_sticky_vehicles = []
    for vehicle_name in active_vehicle_names:
        try:
            vehicle = vehicle_registry.get(vehicle_name)
        except KeyError:
            continue
        sticky_rule = get_sticky_rule(vehicle)
        if sticky_rule and sticky_rule.enabled:
            active_sticky_vehicles.append(vehicle)
    return active_sticky_vehicles


def build_vehicle_context(agent: Agent, location_change, prev_decisions, active_vehicle_names: set[str],
                          vehicle_registry: VehicleRegistry) -> str:
    vehicle_context_parts = []
    owned_from_home_vehicles = _home_vehicles(agent)
    owned_vehicle_labels = [vehicle.prompt_reference for vehicle in owned_from_home_vehicles]
    leaving_home = location_change.from_building.polygon_id == agent.home.polygon_id

    if leaving_home:
        if len(owned_vehicle_labels) == 1:
            vehicle_context_parts.append(
                f"You are starting from home and you have a {owned_vehicle_labels[0]}, so you may take it if you wish."
            )
        elif len(owned_vehicle_labels) > 1:
            vehicle_context_parts.append(
                f"You are starting from home and you have {_join_labels(owned_vehicle_labels)}, so you may take one if you wish."
            )
        else:
            vehicle_context_parts.append(
                "You are starting from home and you do not have any personal vehicles available to take with you."
            )
    else:
        active_sticky_vehicles = _sticky_vehicles(active_vehicle_names, vehicle_registry)
        until_home_vehicles = []
        for vehicle in active_sticky_vehicles:
            lifecycle = get_lifecycle(vehicle)
            if lifecycle is None:
                continue
            if lifecycle.deactivation == LifecycleDeactivationState.AT_HOME:
                until_home_vehicles.append(vehicle.prompt_reference)

        if until_home_vehicles:
            vehicle_context_parts.append(
                f"You are currently away from home and still have {_join_labels(until_home_vehicles)} with you, so where possible you should continue using it until you return home."
            )
        elif owned_vehicle_labels:
            vehicle_context_parts.append(
                f"You are currently away from home, so you cannot start using {_join_labels(owned_vehicle_labels)} because you did not take one with you."
            )
        else:
            vehicle_context_parts.append(
                "You are currently away from home, so you cannot start using a personal vehicle because you do not have one with you."
            )

    activation_cost_vehicle_map = {
        vehicle.name: vehicle
        for vehicle in get_activation_cost_vehicles(vehicle_registry)
    }
    active_activation_cost_vehicles = [
        activation_cost_vehicle_map[vehicle_name]
        for vehicle_name in active_vehicle_names
        if vehicle_name in activation_cost_vehicle_map
    ]
    carry_style_vehicles = []
    pass_style_vehicles = []
    for vehicle in active_activation_cost_vehicles:
        lifecycle = get_lifecycle(vehicle)
        if lifecycle and lifecycle.activation == LifecycleActivationState.AUTOMATIC:
            pass_style_vehicles.append(vehicle.prompt_reference)
        elif lifecycle and lifecycle.deactivation in {
            LifecycleDeactivationState.AT_HOME,
            LifecycleDeactivationState.AT_STOP,
        }:
            carry_style_vehicles.append(vehicle.prompt_reference)
        else:
            pass_style_vehicles.append(vehicle.prompt_reference)

    if carry_style_vehicles:
        vehicle_context_parts.append(
            f"You currently have {_join_labels(carry_style_vehicles)} active and can keep using it for free as long as you keep it with you."
        )
    if pass_style_vehicles:
        vehicle_context_parts.append(
            f"You already have {_join_labels(pass_style_vehicles)} active, so you can use that mode of transport for free."
        )

    vehicle_context_parts.append("Consider this first when making your choice.\n")
    return " ".join(vehicle_context_parts)


def build_rule_text(vehicle_registry: VehicleRegistry) -> str:
    ownership_required = []
    first_use_from_home = []
    activation_cost_vehicles = get_activation_cost_vehicles(vehicle_registry)
    sticky_until_home = []
    sticky_until_home_exceptions = []
    conflict_lines = []

    for vehicle in vehicle_registry.vehicles:
        lifecycle = get_lifecycle(vehicle)
        for rule in vehicle.rules:
            if isinstance(rule, OwnershipRule) and rule.state == OwnershipState.REQUIRED:
                ownership_required.append(vehicle.prompt_reference)
            elif isinstance(rule, FirstUseLocationRule) and rule.state == FirstUseLocationState.FROM_HOME:
                first_use_from_home.append(vehicle.prompt_reference)
            elif isinstance(rule, StickyRule) and rule.enabled and lifecycle is not None:
                if lifecycle.deactivation == LifecycleDeactivationState.AT_HOME:
                    sticky_until_home.append(vehicle.prompt_reference)
                    for exception in rule.exceptions:
                        sticky_until_home_exceptions.append(
                            _format_sticky_exception(vehicle.prompt_reference, exception, vehicle_registry)
                        )

    prompt_parts = []
    if ownership_required:
        prompt_parts.append(
            f"You can only use {_join_labels(ownership_required)} if you own one."
        )
    if first_use_from_home:
        prompt_parts.append(
            f"You can only start using {_join_labels(first_use_from_home)} on legs where you leave home."
        )
    if activation_cost_vehicles:
        onstart_labels = [vehicle.prompt_reference for vehicle in activation_cost_vehicles if get_lifecycle(vehicle).activation == LifecycleActivationState.ON_START]
        automatic_labels = [vehicle.prompt_reference for vehicle in activation_cost_vehicles if get_lifecycle(vehicle).activation == LifecycleActivationState.AUTOMATIC]
        if onstart_labels:
            prompt_parts.append(
                f"To take {_join_labels(onstart_labels)} you must pay an upfront fee once. It will then be free for the rest of the day. "
                f"Consider how many times it can be reused throughout the day."
            )
        if sticky_until_home:
            prompt_parts.append(
                f"If you start using {_join_labels(sticky_until_home)} when leaving home, you must keep using that vehicle until you return home."
                f"In the event that a {_join_labels(sticky_until_home)} route is unavailable, you can choose an alternative that falls under the following exceptions:"
            )
        if automatic_labels:
            prompt_parts.append(
                f"To take {_join_labels(automatic_labels)} you must pay an upfront fee once. It will then be free for the rest of the month. "
                f"Consider how many times it can be reused throughout the month."
            )
        prompt_parts.extend(sticky_until_home_exceptions)
    prompt_parts.extend(conflict_lines)

    if not prompt_parts:
        return ""

    return "\n".join(prompt_parts) + "\n"


def format_day_outcomes(agent: Agent) -> str:
    journey_outcomes = getattr(agent, "journey_outcomes", None) or []
    outcome_lines = []
    for journey_outcome in journey_outcomes:
        outcome_lines.extend(journey_outcome.build_prompt_lines())

    if not outcome_lines:
        return ""

    return (
        "Here is how your journeys went yesterday:\n"
        + "\n".join(outcome_lines)
        + "\n"
    )
