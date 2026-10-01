from enum import Enum
from dataclasses import dataclass, field
from model.cost_functions import COST_FUNCTIONS, get_cost_function
from model.rule import (
    LifecycleConfig,
    OwnershipRule,
    OwnershipState,
    Rule,
    build_rules,
    validate_rule_config,
)

VALID_VEHICLE_CONFIG_FIELDS = {
    "SUMO_typeID",
    "cost",
    "execution_kind",
    "name",
    "ownable",
    "ownership_probability",
    "prompt_reference",
    "rules",
    "taxi_fleet_file",
    "taxi_line",
}


def _normalize_prompt(prompt_reference: str) -> str:
    return prompt_reference.strip().lower()

class ExecutionKind(str, Enum):
    VEHICLE = "vehicle"
    PEDESTRIAN = "pedestrian"
    INTERMODAL = "intermodal"
    TAXI = "taxi"

@dataclass
class Vehicle:
    name: str
    prompt_reference: str
    sumo_type_id: str
    execution_kind: ExecutionKind
    cost: str | None = None
    ownable: bool = False
    ownership_probability: dict | None = None
    taxi_line: str | None = None
    taxi_fleet_file: str | None = None
    lifecycle: LifecycleConfig | None = None
    rules: list[Rule] = field(default_factory=list)

    def validate(self):
        
        
        # Vehicle can't have ownership rules if it's not ownable
        ownership_rule = next((rule for rule in self.rules if rule.category == "ownership"), None)
        if (
            isinstance(ownership_rule, OwnershipRule)
            and not self.ownable
            and ownership_rule.state in {OwnershipState.OPTIONAL, OwnershipState.REQUIRED}
        ):
            raise ValueError(
                f"Vehicle '{self.name}' has ownable=False but ownership rule "
                f"is '{ownership_rule.state.value}'."
            )
        # If no cost function is defined, set cost to free
        if not self.cost:
            self.cost = "free"     
        # Raise exception if cost function is not recognised  
        elif self.cost not in COST_FUNCTIONS:
            raise ValueError(
                f"Vehicle '{self.name}' references unknown cost function '{self.cost}'."
            )

    @classmethod
    def from_dict(cls, data: dict) -> "Vehicle":
        unknown_fields = sorted(set(data) - VALID_VEHICLE_CONFIG_FIELDS)
        if unknown_fields:
            raise ValueError(
                ", ".join(
                    f"'{field_name}' is not a valid vehicle configuration field"
                    for field_name in unknown_fields
                )
            )

        if "name" not in data:
            raise ValueError("Vehicle configuration must define 'name'.")

        execution_kind = data["execution_kind"]
        if execution_kind == "public_transport":
            execution_kind = ExecutionKind.INTERMODAL.value
        parsed_rules = build_rules(data.get("rules"))

        vehicle = cls(
            name=data["name"],
            prompt_reference=data.get("prompt_reference", data["name"]),
            sumo_type_id=data["SUMO_typeID"],
            execution_kind=ExecutionKind(execution_kind),
            cost=data.get("cost", "free"),
            ownable=data.get("ownable", False),
            ownership_probability=data.get("ownership_probability"),
            taxi_line=data.get("taxi_line"),
            taxi_fleet_file=data.get("taxi_fleet_file"),
            lifecycle=parsed_rules.lifecycle,
            rules=parsed_rules.rules,
        )
        vehicle.validate()
        return vehicle

    def to_dict(self) -> dict:
        rules_dict = {}
        sticky_dict = None
        conflict_list = None

        for rule in self.rules:
            if rule.category == "sticky":
                sticky_dict = rule.to_dict()
                continue
            if rule.category == "conflict":
                conflict_list = rule.to_dict()
                continue
            rules_dict[rule.category] = rule.to_dict()

        if self.lifecycle is not None:
            lifecycle_dict = self.lifecycle.to_dict()
            if sticky_dict is not None:
                lifecycle_dict["sticky"] = sticky_dict
            if conflict_list is not None:
                lifecycle_dict["conflict"] = conflict_list
            rules_dict["lifecycle"] = lifecycle_dict

        return {
            "name": self.name,
            "prompt_reference": self.prompt_reference,
            "SUMO_typeID": self.sumo_type_id,
            "execution_kind": self.execution_kind.value,
            "cost": self.cost,
            "ownable": self.ownable,
            "ownership_probability": self.ownership_probability,
            "taxi_line": self.taxi_line,
            "taxi_fleet_file": self.taxi_fleet_file,
            "rules": rules_dict,
        }

    def cost_function(self):
        return get_cost_function(self.cost)


@dataclass
class VehicleRegistry:
    vehicles: list[Vehicle]
    validation_registry: "VehicleRegistry | None" = None

    def __post_init__(self):
        self.by_name = {vehicle.name: vehicle for vehicle in self.vehicles}
        self.ownable = [vehicle for vehicle in self.vehicles if vehicle.ownable]
        registry_for_validation = self.validation_registry or self
        for vehicle in self.vehicles:
            validate_rule_config(vehicle, registry_for_validation)

    @classmethod
    def from_configs(cls, vehicle_configs: list[dict], validation_registry: "VehicleRegistry | None" = None) -> "VehicleRegistry":
        vehicles = [Vehicle.from_dict(vehicle_config) for vehicle_config in vehicle_configs]
        names = []
        for vehicle in vehicles:
            if vehicle.name in names:
                raise ValueError(f"Duplicate vehicle name: '{vehicle.name}'. " 
                                 f"All vehicles must have a unique identifying name, but can have identical prompt references.")
            names.append(vehicle.name)
        return cls(vehicles, validation_registry=validation_registry)

    def get(self, name: str) -> Vehicle:
        return self.by_name[name]

    def get_by_prompt(self, prompt_reference: str) -> Vehicle:
        normalized = _normalize_prompt(prompt_reference)

        for vehicle in self.vehicles:
            if _normalize_prompt(vehicle.prompt_reference) == normalized:
                return vehicle
            if _normalize_prompt(vehicle.name) == normalized:
                return vehicle
        raise KeyError(prompt_reference)


def build_day_registry(
    vehicle_configs: list[dict],
    validation_registry: "VehicleRegistry | None" = None,
    active_vehicle_names: list[str] | None = None,
    inactive_vehicle_names: list[str] | None = None,
    context_label: str = "unknown day",
) -> VehicleRegistry:
    active_vehicle_names = list(active_vehicle_names or [])
    inactive_vehicle_names = list(inactive_vehicle_names or [])

    if active_vehicle_names and inactive_vehicle_names:
        raise ValueError("Cannot pass both active_vehicle_names and inactive_vehicle_names.")

    all_vehicle_names = {vehicle_config["name"] for vehicle_config in vehicle_configs}
    referenced_vehicle_names = active_vehicle_names + inactive_vehicle_names
    missing_vehicle_names = [
        vehicle_name for vehicle_name in referenced_vehicle_names
        if vehicle_name not in all_vehicle_names
    ]
    if missing_vehicle_names:
        raise ValueError(
            f"Vehicle selection for {context_label} references unknown vehicles: {missing_vehicle_names}"
        )

    if active_vehicle_names:
        filtered_vehicle_configs = [
            vehicle_config
            for vehicle_config in vehicle_configs
            if vehicle_config["name"] in set(active_vehicle_names)
        ]
    elif inactive_vehicle_names:
        filtered_vehicle_configs = [
            vehicle_config
            for vehicle_config in vehicle_configs
            if vehicle_config["name"] not in set(inactive_vehicle_names)
        ]
    else:
        filtered_vehicle_configs = list(vehicle_configs)

    if not filtered_vehicle_configs:
        raise ValueError(f"Vehicle selection for {context_label} leaves no active vehicles.")

    vehicle_registry = VehicleRegistry.from_configs(
        filtered_vehicle_configs,
        validation_registry=validation_registry,
    )

    seen_prompt_references: dict[str, str] = {}
    duplicate_prompt_references: list[str] = []
    for vehicle in vehicle_registry.vehicles:
        normalized_prompt_reference = _normalize_prompt(vehicle.prompt_reference)
        if normalized_prompt_reference in seen_prompt_references:
            duplicate_prompt_references.append(vehicle.prompt_reference)
        else:
            seen_prompt_references[normalized_prompt_reference] = vehicle.name

    if duplicate_prompt_references:
        raise ValueError(
            "Active vehicles must have unique prompt_reference values. "
            f"Duplicates found for {context_label}: {duplicate_prompt_references}"
        )

    return vehicle_registry
