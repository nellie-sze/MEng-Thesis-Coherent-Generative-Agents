import json
import geopandas as gpd

class EventConfig:
    def __init__(self, name, start_time, end_time, location_id, description):
        self.name = name
        self.start_time = start_time
        self.end_time = end_time
        self.location_id = location_id
        self.description = description

    def to_dict(self):
        return {
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "location_id": self.location_id,
            "description": self.description,
        }

    def to_json(self):
        return json.dumps(self.to_dict())

    @classmethod
    def validate_event_config(cls, data, run_config, valid_location_ids=None):
        if isinstance(data, str):
            data = json.loads(data)

        name = data["name"]
        start_time = data["start_time"]
        end_time = data["end_time"]
        location_id = data["location_id"]
        description = data["description"]

        if not isinstance(name, str) or not name.strip():
            raise ValueError("Event config requires a non-empty string name.")
        cls._validate_time(start_time, "start_time")
        cls._validate_time(end_time, "end_time")
        if valid_location_ids is None:
            valid_location_ids = cls.load_valid_location_ids(run_config)
        cls._validate_location_id(location_id, valid_location_ids)
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Event config requires a non-empty string description.")

        return cls(
            name=name.strip(),
            start_time=start_time,
            end_time=end_time,
            location_id=str(location_id),
            description=description.strip(),
        )

    @classmethod
    def from_json(cls, data, run_config=None, valid_location_ids=None):
        if run_config is None and valid_location_ids is None:
            raise ValueError("EventConfig.from_json requires run_config or valid_location_ids for location validation.")
        return cls.validate_event_config(data, run_config, valid_location_ids=valid_location_ids)

    @staticmethod
    def _validate_time(value, field_name):
        if not isinstance(value, str):
            raise ValueError(f"Event config field {field_name!r} must be a string in HH:MM format.")

        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError(f"Event config field {field_name!r} must be in HH:MM format.")

        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError as exc:
            raise ValueError(f"Event config field {field_name!r} must be in HH:MM format.") from exc

        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"Event config field {field_name!r} must be in HH:MM format.")

    @staticmethod
    def _validate_location_id(location_id, valid_location_ids=None):
        if location_id is None or str(location_id).strip() == "":
            raise ValueError("Event config requires a non-empty location_id.")
        if valid_location_ids is not None and str(location_id) not in valid_location_ids:
            raise ValueError(f"Unknown location_id {location_id!r}; it is not a valid polygon id in the active study area.")

    @staticmethod
    def load_valid_location_ids(run_config):
        if run_config is None:
            raise ValueError("run_config is required for polygon id validation.")
        buildings_file = run_config.get("buildings_file")
        if not buildings_file:
            raise ValueError("run_config must include buildings_file for polygon id validation.")
        buildings = gpd.read_file(buildings_file)
        if "id" not in buildings.columns:
            raise ValueError(f"Building layer {buildings_file!r} does not contain an 'id' column for polygon ids.")
        return {str(polygon_id) for polygon_id in buildings["id"].dropna().astype(str).tolist()}
