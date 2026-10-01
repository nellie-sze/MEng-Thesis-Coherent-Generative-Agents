import importlib.util
import json
from pathlib import Path

from model.event_config import EventConfig

WEEKDAYS = {
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
}

VALID_DAILY_CONTEXT_FIELDS = {
    "activate",
    "deactivate",
    "environmental_context",
    "events",
}


class DailyContext:
    def __init__(self, events, environmental_context=None, day=None, activate=None, deactivate=None):
        self.events = events
        self.environmental_context = environmental_context
        self.day = day
        self.activate = activate or []
        self.deactivate = deactivate or []

    def to_dict(self):
        return {
            "day": self.day,
            "events": [event.to_dict() for event in self.events],
            "environmental_context": self.environmental_context,
            "activate": self.activate,
            "deactivate": self.deactivate,
        }

    def to_json(self):
        return json.dumps(self.to_dict())

    @classmethod
    def empty(cls):
        return cls(events=[], environmental_context=None, day=None, activate=[], deactivate=[])

    def with_day(self, day):
        return DailyContext(
            events=self.events,
            environmental_context=self.environmental_context,
            day=day,
            activate=self.activate,
            deactivate=self.deactivate,
        )

    @classmethod
    def validate_daily_context(cls, data, run_config, valid_location_ids=None):
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise ValueError("Each daily context must be an object.")

        unknown_fields = sorted(set(data) - VALID_DAILY_CONTEXT_FIELDS)
        if unknown_fields:
            raise ValueError(
                ", ".join(
                    f"'{field_name}' is not a valid daily_context configuration field"
                    for field_name in unknown_fields
                )
            )

        raw_events = data.get("events", [])
        if not isinstance(raw_events, list):
            raise ValueError("Daily context field 'events' must be a list.")
        activate = data.get("activate", [])
        deactivate = data.get("deactivate", [])
        if activate and deactivate:
            raise ValueError("Daily context may define either 'activate' or 'deactivate', but not both.")
        if not isinstance(activate, list):
            raise ValueError("Daily context field 'activate' must be a list.")
        if not isinstance(deactivate, list):
            raise ValueError("Daily context field 'deactivate' must be a list.")
        if any(not isinstance(vehicle_name, str) for vehicle_name in activate):
            raise ValueError("Daily context field 'activate' must contain only vehicle-name strings.")
        if any(not isinstance(vehicle_name, str) for vehicle_name in deactivate):
            raise ValueError("Daily context field 'deactivate' must contain only vehicle-name strings.")

        if valid_location_ids is None:
            valid_location_ids = EventConfig.load_valid_location_ids(run_config)

        return cls(
            events=[
                EventConfig.validate_event_config(
                    event,
                    run_config,
                    valid_location_ids=valid_location_ids,
                )
                for event in raw_events
            ],
            environmental_context=data.get("environmental_context"),
            activate=activate,
            deactivate=deactivate,
        )

    @classmethod
    def from_json(cls, data, run_config, valid_location_ids=None):
        return cls.validate_daily_context(data, run_config, valid_location_ids=valid_location_ids)


class ValidatedDayConfig:
    def __init__(self, num_days, start_day, daily_contexts, regenerate_descriptions=True):
        self.num_days = num_days
        self.start_day = start_day
        self.daily_contexts = daily_contexts
        self.regenerate_descriptions = regenerate_descriptions

    def to_dict(self):
        return {
            "num_days": self.num_days,
            "start_day": self.start_day,
            "regenerate_descriptions": self.regenerate_descriptions,
            "daily_contexts": [daily_context.to_dict() for daily_context in self.daily_contexts],
        }


def _load_day_config(path):
    config_path = Path(path).resolve()
    spec = importlib.util.spec_from_file_location("day_config_runtime", config_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load day config module from {config_path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_day_config(path, run_config):
    module = _load_day_config(path)
    num_days = getattr(module, "NUM_DAYS", None)
    start_day = getattr(module, "START_DAY", None)
    regenerate_descriptions = getattr(module, "REGENERATE_DESCRIPTIONS", True)
    daily_contexts = getattr(module, "DAILY_CONTEXTS", None)

    if not isinstance(num_days, int):
        raise ValueError("Day config must define NUM_DAYS as an integer.")
    if num_days < 1:
        raise ValueError("num_days must be at least 1.")
    if not isinstance(start_day, str):
        raise ValueError("Day config must define START_DAY as a weekday string.")
    if start_day not in WEEKDAYS:
        raise ValueError(f"Unsupported start day: {start_day!r}.")
    if not isinstance(regenerate_descriptions, bool):
        raise ValueError("Day config field REGENERATE_DESCRIPTIONS must be a boolean.")
    if not isinstance(daily_contexts, list):
        raise ValueError("DAILY_CONTEXTS must be a list.")

    valid_location_ids = EventConfig.load_valid_location_ids(run_config)
    validated_daily_contexts = [
        DailyContext.validate_daily_context(
            daily_context,
            run_config,
            valid_location_ids=valid_location_ids,
        )
        for daily_context in daily_contexts
    ]
    if len(validated_daily_contexts) < num_days:
        validated_daily_contexts.extend(
            DailyContext.empty() for _ in range(num_days - len(validated_daily_contexts))
        )
    else:
        validated_daily_contexts = validated_daily_contexts[:num_days]
    return ValidatedDayConfig(
        num_days=num_days,
        start_day=start_day,
        regenerate_descriptions=regenerate_descriptions,
        daily_contexts=validated_daily_contexts,
    )
