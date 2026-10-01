from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TYPE_CHECKING

from util.time import time_to_seconds

if TYPE_CHECKING:
    from model.agent import Agent
    from model.vehicle import Vehicle, VehicleRegistry


class OwnershipState(str, Enum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


class FirstUseLocationState(str, Enum):
    ANYWHERE = "anywhere"
    FROM_HOME = "from_home"


class LifecycleActivationState(str, Enum):
    NONE = "none"
    ON_START = "on_start"
    AUTOMATIC = "automatic"


class LifecycleDeactivationState(str, Enum):
    NONE = "none"
    AT_HOME = "at_home"
    AT_STOP = "at_stop"
    END_OF_DAY = "end_of_day"
    NEVER = "never"


@dataclass
class RuleException:
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class LifecycleConfig:
    activation: LifecycleActivationState
    deactivation: LifecycleDeactivationState
    linked_attribute: str | None = None
    linked_value: Any = None

    def creates_active_state(self) -> bool:
        return (
            self.activation != LifecycleActivationState.NONE
            and self.deactivation != LifecycleDeactivationState.NONE
        )

    def to_dict(self) -> dict[str, Any]:
        data = {
            "activation": self.activation.value,
            "deactivation": self.deactivation.value,
        }
        if self.linked_attribute is not None:
            data["linked_attribute"] = self.linked_attribute
        if self.linked_value is not None:
            data["linked_value"] = self.linked_value
        return data


@dataclass
class ParsedRules:
    rules: list["Rule"]
    lifecycle: LifecycleConfig | None = None


@dataclass
class Rule:
    category: str

    def evaluate(self, context, agent: "Agent", vehicle_name: str, vehicle_registry: "VehicleRegistry") -> tuple[bool, str | None]:
        violations = self.evaluate_all(context, agent, vehicle_name, vehicle_registry)
        if violations:
            return True, violations[0][1]
        return False, None

    def evaluate_all(self, context, agent: "Agent", vehicle_name: str, vehicle_registry: "VehicleRegistry") -> list[tuple["LegContext", str]]:
        raise NotImplementedError

    def explain(self) -> str:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any] | str | list[str]:
        raise NotImplementedError


@dataclass
class OwnershipRule(Rule):
    state: OwnershipState

    def evaluate_all(self, context, agent: "Agent", vehicle_name: str, vehicle_registry: "VehicleRegistry") -> list[tuple["LegContext", str]]:
        if self.state in {OwnershipState.NONE, OwnershipState.OPTIONAL}:
            return []

        owned_vehicles = {vehicle.name for vehicle in agent.owned_vehicles}
        if vehicle_name in owned_vehicles:
            return []

        violations = []
        for leg in context:
            if leg.vehicle_choice == vehicle_name:
                violations.append((leg, f"Used {vehicle_name} without owning one."))
        return violations

    def explain(self) -> str:
        return f"Ownership rule: {self.state.value}."

    def to_dict(self) -> str:
        return self.state.value


@dataclass
class FirstUseLocationRule(Rule):
    state: FirstUseLocationState

    def evaluate_all(self, context, agent: "Agent", vehicle_name: str, vehicle_registry: "VehicleRegistry") -> list[tuple["LegContext", str]]:
        if self.state == FirstUseLocationState.ANYWHERE:
            return []

        vehicle = vehicle_registry.get(vehicle_name)
        lifecycle = get_lifecycle(vehicle)
        pre_leg_active = None
        if lifecycle is not None and lifecycle.creates_active_state():
            pre_leg_active = build_active_timeline(
                context,
                agent,
                vehicle_registry,
                retroactive_automatic=False,
            )

        violations = []
        used = False
        for index, leg in enumerate(context):
            if leg.vehicle_choice != vehicle_name:
                continue

            is_new_episode = vehicle_name not in pre_leg_active[index] if pre_leg_active is not None else not used
            if is_new_episode and not leg.from_home:
                violations.append((leg, f"Started using {vehicle_name} after leaving home."))
            used = True
        return violations

    def explain(self) -> str:
        return f"First use location rule: {self.state.value}."

    def to_dict(self) -> str:
        return self.state.value


@dataclass
class StickyRule(Rule):
    enabled: bool
    exceptions: list[RuleException] = field(default_factory=list)

    def evaluate_all(self, context, agent: "Agent", vehicle_name: str, vehicle_registry: "VehicleRegistry") -> list[tuple["LegContext", str]]:
        if not self.enabled:
            return []

        pre_leg_active = build_active_timeline(
            context,
            agent,
            vehicle_registry,
            retroactive_automatic=True,
        )

        violations = []
        for leg, active_vehicle_names in zip(context, pre_leg_active):
            if vehicle_name not in active_vehicle_names:
                continue
            if leg.vehicle_choice == vehicle_name:
                continue
            if matches_sticky(vehicle_name, leg, vehicle_registry):
                continue
            violations.append((leg, _sticky_violation_message(vehicle_name, vehicle_registry)))
        return violations

    def explain(self) -> str:
        return f"Sticky rule: {'enabled' if self.enabled else 'disabled'}."

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "exceptions": [exception.data for exception in self.exceptions],
        }


@dataclass
class ConflictRule(Rule):
    conflicting_vehicles: list[str] = field(default_factory=list)

    def evaluate_all(self, context, agent: "Agent", vehicle_name: str, vehicle_registry: "VehicleRegistry") -> list[tuple["LegContext", str]]:
        if not self.conflicting_vehicles:
            return []

        pre_leg_active = build_active_timeline(
            context,
            agent,
            vehicle_registry,
            retroactive_automatic=True,
        )

        violations = []
        for leg, active_vehicle_names in zip(context, pre_leg_active):
            if vehicle_name not in active_vehicle_names:
                continue
            if leg.vehicle_choice not in self.conflicting_vehicles:
                continue
            violations.append((leg, f"Chose conflicting vehicle {leg.vehicle_choice} while {vehicle_name} was still active."))
        return violations

    def explain(self) -> str:
        return f"Conflict rule: {', '.join(self.conflicting_vehicles)}."

    def to_dict(self) -> list[str]:
        return list(self.conflicting_vehicles)


@dataclass
class LegContext:
    agent_id: int
    route_id: str
    leg_index: int
    vehicle_choice: str | None
    possible_mot: list[str]
    possible_modes: str
    from_home: bool
    to_home: bool
    from_polygon_id: str | None
    to_polygon_id: str | None
    from_x: float | None
    from_y: float | None
    to_x: float | None
    to_y: float | None
    departure_time: int | None
    arrival_time: int | None
    duration_minutes: float | None
    distance_km: float | None


@dataclass
class RuleEvaluationContext:
    agent_id: int
    home_polygon_id: str | None
    owned_vehicle_names: set[str] = field(default_factory=set)
    used_vehicle_names: set[str] = field(default_factory=set)
    legs: list[LegContext] = field(default_factory=list)


def parse_state(category: str, state_value: str, enum_cls):
    try:
        return enum_cls(state_value)
    except ValueError as exc:
        allowed_values = ", ".join(state.value for state in enum_cls)
        raise ValueError(
            f"Invalid value {state_value!r} for rule {category!r}. Allowed values: {allowed_values}"
        ) from exc


def parse_lifecycle(rules_data: dict[str, Any] | None) -> LifecycleConfig | None:
    if not rules_data or "lifecycle" not in rules_data:
        return None

    lifecycle_data = rules_data["lifecycle"]
    if not isinstance(lifecycle_data, dict):
        raise ValueError("Rule 'lifecycle' must be a dictionary.")

    if "activation" not in lifecycle_data or "deactivation" not in lifecycle_data:
        raise ValueError("Rule 'lifecycle' must define both 'activation' and 'deactivation'.")

    activation = parse_state("lifecycle.activation", lifecycle_data["activation"], LifecycleActivationState)
    deactivation = parse_state("lifecycle.deactivation", lifecycle_data["deactivation"], LifecycleDeactivationState)
    linked_attribute = lifecycle_data.get("linked_attribute")
    linked_value = lifecycle_data.get("linked_value")

    if activation == LifecycleActivationState.AUTOMATIC:
        if linked_attribute is None or "linked_value" not in lifecycle_data:
            raise ValueError("Automatic lifecycle activation requires both 'linked_attribute' and 'linked_value'.")
    elif linked_attribute is not None or "linked_value" in lifecycle_data:
        raise ValueError("'linked_attribute' and 'linked_value' are only valid for automatic lifecycle activation.")

    sticky_data = lifecycle_data.get("sticky")
    conflict_data = lifecycle_data.get("conflict")
    if (
        (sticky_data is not None or conflict_data is not None)
        and (activation == LifecycleActivationState.NONE or deactivation == LifecycleDeactivationState.NONE)
    ):
        raise ValueError("Sticky/conflict lifecycle settings require both activation and deactivation to be defined.")

    if sticky_data is not None:
        if not isinstance(sticky_data, dict):
            raise ValueError("Rule 'lifecycle.sticky' must be a dictionary.")
        if "enabled" not in sticky_data:
            raise ValueError("Rule 'lifecycle.sticky' must define 'enabled'.")
        if sticky_data.get("exceptions") and not sticky_data.get("enabled"):
            raise ValueError("Sticky exceptions are only valid when 'sticky.enabled' is True.")

    if conflict_data is not None and not isinstance(conflict_data, list):
        raise ValueError("Rule 'lifecycle.conflict' must be a list of vehicle names.")

    return LifecycleConfig(
        activation=activation,
        deactivation=deactivation,
        linked_attribute=linked_attribute,
        linked_value=linked_value,
    )


def _build_simple_rule(category: str, rule_value: str | dict[str, Any]) -> Rule:
    state_value = rule_value["state"] if isinstance(rule_value, dict) else rule_value

    if category == "ownership":
        return OwnershipRule(
            category=category,
            state=parse_state(category, state_value, OwnershipState),
        )

    if category == "first_use_location":
        return FirstUseLocationRule(
            category=category,
            state=parse_state(category, state_value, FirstUseLocationState),
        )

    raise ValueError(f"Unknown rule category: {category}")


def build_rules(rules_data: dict[str, Any] | None) -> ParsedRules:
    if not rules_data:
        return ParsedRules(rules=[])

    lifecycle = parse_lifecycle(rules_data)
    rules: list[Rule] = []

    for category, rule_value in rules_data.items():
        if category == "lifecycle":
            continue
        rules.append(_build_simple_rule(category, rule_value))

    if lifecycle is not None:
        sticky_data = rules_data["lifecycle"].get("sticky")
        if sticky_data is not None:
            exceptions = [
                RuleException(data=exception_data)
                for exception_data in sticky_data.get("exceptions", [])
            ]
            rules.append(StickyRule(
                category="sticky",
                enabled=bool(sticky_data.get("enabled", False)),
                exceptions=exceptions,
            ))

        conflict_data = rules_data["lifecycle"].get("conflict")
        if conflict_data:
            rules.append(ConflictRule(
                category="conflict",
                conflicting_vehicles=list(conflict_data),
            ))

    return ParsedRules(rules=rules, lifecycle=lifecycle)


def validate_rule_config(vehicle: "Vehicle", vehicle_registry: "VehicleRegistry") -> None:
    for rule in vehicle.rules:
        if isinstance(rule, ConflictRule):
            for conflicting_vehicle_name in rule.conflicting_vehicles:
                if conflicting_vehicle_name not in vehicle_registry.by_name:
                    raise ValueError(
                        f"Vehicle '{vehicle.name}' references unknown conflicting vehicle '{conflicting_vehicle_name}'."
                    )
        if isinstance(rule, StickyRule):
            if rule.enabled and vehicle.lifecycle is None:
                raise ValueError(
                    f"Vehicle '{vehicle.name}' has sticky rule enabled but no lifecycle defined, which is required for sticky behaviour."
                )
            if rule.enabled and vehicle.lifecycle.deactivation == LifecycleDeactivationState.AT_STOP:
                raise ValueError(
                    f"Vehicle '{vehicle.name}' has sticky rule enabled but lifecycle deactivation is 'at_stop', which triggers perpetual sticky violations."
                )
            for exception in rule.exceptions:
                allowed_vehicles = exception.data.get("allowed_vehicles", [])
                for allowed_vehicle_name in allowed_vehicles:
                    if allowed_vehicle_name not in vehicle_registry.by_name:
                        raise ValueError(
                            f"Vehicle '{vehicle.name}' has sticky rule with exception referencing unknown vehicle '{allowed_vehicle_name}'."
                        )


def get_rule(vehicle: "Vehicle", category: str) -> Rule | None:
    for rule in vehicle.rules:
        if rule.category == category:
            return rule
    return None


def get_first_use_rule(vehicle: "Vehicle") -> FirstUseLocationRule | None:
    rule = get_rule(vehicle, "first_use_location")
    return rule if isinstance(rule, FirstUseLocationRule) else None


def get_sticky_rule(vehicle: "Vehicle") -> StickyRule | None:
    rule = get_rule(vehicle, "sticky")
    return rule if isinstance(rule, StickyRule) else None


def get_conflict_rule(vehicle: "Vehicle") -> ConflictRule | None:
    rule = get_rule(vehicle, "conflict")
    return rule if isinstance(rule, ConflictRule) else None


def get_lifecycle(vehicle: "Vehicle") -> LifecycleConfig | None:
    return getattr(vehicle, "lifecycle", None)


def has_active_lifecycle(vehicle: "Vehicle") -> bool:
    lifecycle = get_lifecycle(vehicle)
    return lifecycle.creates_active_state() if lifecycle else False


def find_chosen_route(location_change):
    if not location_change.decision or not location_change.possible_routes:
        return None

    vehicle_name = location_change.decision.get("vehicle_name")
    if vehicle_name is None and location_change.decision.get("means_of_transport"):
        normalized_choice = location_change.decision["means_of_transport"].strip().lower()
        return next(
            (
                route for route in location_change.possible_routes
                if route.vehicle.prompt_reference.lower() == normalized_choice
                or route.vehicle.name.lower() == normalized_choice
            ),
            None,
        )

    return next(
        (route for route in location_change.possible_routes if route.vehicle.name == vehicle_name),
        None,
    )


def build_leg_contexts(agent: "Agent", stop_before_route_id: str | None = None) -> list[LegContext]:
    contexts: list[LegContext] = []

    for leg_index, location_change in enumerate(agent.location_changes or []):
        if stop_before_route_id is not None and str(location_change.route_id) == str(stop_before_route_id):
            break
        if not location_change.decision:
            continue

        chosen_route = find_chosen_route(location_change)
        arrival_time = time_to_seconds(location_change.to_task.time)
        departure_time = None
        travel_time_minutes = 0.0
        distance_km = 0.0

        if chosen_route and chosen_route.travel_time is not None:
            travel_time_minutes = float(chosen_route.travel_time) / 60.0
            if arrival_time is not None:
                departure_time = max(0, int(arrival_time - chosen_route.travel_time))
        if chosen_route and chosen_route.distance is not None:
            distance_km = float(chosen_route.distance) / 1000.0

        possible_mode_names = [route.vehicle.name for route in location_change.possible_routes] if location_change.possible_routes else []
        contexts.append(LegContext(
            agent_id=agent.id,
            route_id=str(location_change.route_id),
            leg_index=leg_index,
            vehicle_choice=location_change.decision.get("vehicle_name"),
            possible_mot=possible_mode_names,
            possible_modes="|".join(possible_mode_names),
            from_home=bool(agent.home and location_change.from_building.polygon_id == agent.home.polygon_id),
            to_home=bool(agent.home and location_change.to_building.polygon_id == agent.home.polygon_id),
            from_polygon_id=location_change.from_building.polygon_id if location_change.from_building else None,
            to_polygon_id=location_change.to_building.polygon_id if location_change.to_building else None,
            from_x=float(location_change.from_building.location.x) if location_change.from_building and location_change.from_building.location else None,
            from_y=float(location_change.from_building.location.y) if location_change.from_building and location_change.from_building.location else None,
            to_x=float(location_change.to_building.location.x) if location_change.to_building and location_change.to_building.location else None,
            to_y=float(location_change.to_building.location.y) if location_change.to_building and location_change.to_building.location else None,
            departure_time=departure_time,
            arrival_time=arrival_time,
            duration_minutes=travel_time_minutes if chosen_route else None,
            distance_km=distance_km if chosen_route else None,
        ))

    return contexts


def _matches_automatic_activation(agent: "Agent", lifecycle: LifecycleConfig) -> bool:
    if lifecycle.activation != LifecycleActivationState.AUTOMATIC or lifecycle.linked_attribute is None:
        return False
    return getattr(agent, lifecycle.linked_attribute, None) == lifecycle.linked_value


def matches_sticky(vehicle_name: str, leg: LegContext, vehicle_registry: "VehicleRegistry") -> bool:
    try:
        vehicle = vehicle_registry.get(vehicle_name)
    except KeyError:
        return False

    sticky_rule = get_sticky_rule(vehicle)
    if sticky_rule is None or not sticky_rule.enabled:
        return False

    for exception in sticky_rule.exceptions:
        allowed_vehicles = exception.data.get("allowed_vehicles", [])
        duration_threshold = exception.data.get("duration_threshold")
        distance_threshold = exception.data.get("distance_threshold")

        vehicle_ok = True if not allowed_vehicles else leg.vehicle_choice in allowed_vehicles
        duration_ok = True if duration_threshold is None else (
            leg.duration_minutes is not None and leg.duration_minutes <= duration_threshold
        )
        distance_ok = True if distance_threshold is None else (
            leg.distance_km is not None and leg.distance_km <= distance_threshold
        )

        if vehicle_ok and duration_ok and distance_ok:
            return True
    return False


def _advance_active_vehicle_names(
    active_vehicle_names: set[str],
    leg: LegContext,
    agent: "Agent",
    vehicle_registry: "VehicleRegistry",
) -> set[str]:
    updated_active_vehicle_names = set(active_vehicle_names)
    chosen_vehicle_name = leg.vehicle_choice

    for active_vehicle_name in list(updated_active_vehicle_names):
        vehicle = vehicle_registry.get(active_vehicle_name)
        lifecycle = get_lifecycle(vehicle)
        if lifecycle is None or not lifecycle.creates_active_state():
            updated_active_vehicle_names.discard(active_vehicle_name)
            continue

        if chosen_vehicle_name == active_vehicle_name:
            if lifecycle.deactivation == LifecycleDeactivationState.AT_HOME and leg.to_home:
                updated_active_vehicle_names.discard(active_vehicle_name)
            continue

        if lifecycle.deactivation == LifecycleDeactivationState.AT_STOP:
            if matches_sticky(active_vehicle_name, leg, vehicle_registry):
                continue
            updated_active_vehicle_names.discard(active_vehicle_name)

    if chosen_vehicle_name is None:
        return updated_active_vehicle_names

    try:
        chosen_vehicle = vehicle_registry.get(chosen_vehicle_name)
    except KeyError:
        return updated_active_vehicle_names

    lifecycle = get_lifecycle(chosen_vehicle)
    if lifecycle is None or not lifecycle.creates_active_state():
        return updated_active_vehicle_names

    if lifecycle.activation in {LifecycleActivationState.ON_START, LifecycleActivationState.AUTOMATIC}:
        updated_active_vehicle_names.add(chosen_vehicle_name)
        if lifecycle.deactivation == LifecycleDeactivationState.AT_HOME and leg.to_home:
            updated_active_vehicle_names.discard(chosen_vehicle_name)

    return updated_active_vehicle_names


def _automatic_purchase_names(context: list[LegContext], vehicle_registry: "VehicleRegistry") -> set[str]:
    automatic_names: set[str] = set()
    for leg in context:
        if not leg.vehicle_choice:
            continue
        try:
            vehicle = vehicle_registry.get(leg.vehicle_choice)
        except KeyError:
            continue
        lifecycle = get_lifecycle(vehicle)
        if lifecycle and lifecycle.activation == LifecycleActivationState.AUTOMATIC:
            automatic_names.add(vehicle.name)
    return automatic_names


def _seed_active_vehicles(
    agent: "Agent",
    vehicle_registry: "VehicleRegistry",
    automatic_purchase_names: set[str] | None = None,
) -> set[str]:
    active_vehicle_names: set[str] = set()
    automatic_purchase_names = automatic_purchase_names or set()

    for vehicle in vehicle_registry.vehicles:
        lifecycle = get_lifecycle(vehicle)
        if lifecycle is None or not lifecycle.creates_active_state():
            continue
        if lifecycle.activation != LifecycleActivationState.AUTOMATIC:
            continue
        if _matches_automatic_activation(agent, lifecycle) or vehicle.name in automatic_purchase_names:
            active_vehicle_names.add(vehicle.name)

    return active_vehicle_names


def build_active_timeline(
    context: list[LegContext],
    agent: "Agent",
    vehicle_registry: "VehicleRegistry",
    retroactive_automatic: bool = False,
) -> list[set[str]]:
    active_vehicle_names = _seed_active_vehicles(
        agent,
        vehicle_registry,
        _automatic_purchase_names(context, vehicle_registry) if retroactive_automatic else None,
    )

    timeline: list[set[str]] = []
    for leg in context:
        timeline.append(set(active_vehicle_names))
        active_vehicle_names = _advance_active_vehicle_names(
            active_vehicle_names,
            leg,
            agent,
            vehicle_registry,
        )
    return timeline


def get_active_vehicles(agent: "Agent", current_location_change, vehicle_registry: "VehicleRegistry") -> set[str]:
    prior_context = build_leg_contexts(agent, stop_before_route_id=str(current_location_change.route_id))
    timeline = build_active_timeline(
        prior_context,
        agent,
        vehicle_registry,
        retroactive_automatic=False,
    )

    if not prior_context:
        return _seed_active_vehicles(agent, vehicle_registry)

    return _advance_active_vehicle_names(
        timeline[-1],
        prior_context[-1],
        agent,
        vehicle_registry,
    )


def update_linked_attribute(agent: "Agent", vehicle: "Vehicle") -> None:
    lifecycle = get_lifecycle(vehicle)
    if lifecycle is None:
        return
    if lifecycle.activation != LifecycleActivationState.AUTOMATIC:
        return
    if lifecycle.linked_attribute is None:
        return
    setattr(agent, lifecycle.linked_attribute, lifecycle.linked_value)


def _sticky_violation_message(vehicle_name: str, vehicle_registry: "VehicleRegistry") -> str:
    vehicle = vehicle_registry.get(vehicle_name)
    lifecycle = get_lifecycle(vehicle)
    if lifecycle is None:
        return f"Made an inconsistent switch away from {vehicle_name} while it was still active."
    if lifecycle.deactivation == LifecycleDeactivationState.AT_HOME:
        return f"Chose an invalid mode after using {vehicle_name} before returning home."
    if lifecycle.deactivation == LifecycleDeactivationState.END_OF_DAY:
        return f"Chose an invalid mode after activating {vehicle_name} for the day."
    return f"Made an inconsistent switch away from {vehicle_name} while it was still active."
