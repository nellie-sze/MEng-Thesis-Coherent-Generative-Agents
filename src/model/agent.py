import json

import numpy as np

from model.building import Building
from model.day_schedule import DaySchedule
from model.journey_outcome import JourneyOutcome
from model.vehicle import Vehicle
from model.location_change import LocationChange
from model.seed import Seed


class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


class Agent:
    def __init__(self, id):
        self.id = id
        self.seed = None
        self.description = None
        self.day_schedule = None
        self.location_changes = None
        self.home = None
        self.home_type = None
        self.owned_vehicles = []
        self.travel_pass = None
        self.route_descriptions = None
        self.journey_outcomes = None

    def reset_day_state(self, preserve_journey_outcomes=False):
        self.day_schedule = None
        self.location_changes = None
        self.route_descriptions = None
        if not preserve_journey_outcomes:
            self.journey_outcomes = None

    def to_dict(self):
        return {
            "id": self.id,
            "seed": self.seed.to_dict() if self.seed else None,
            "description": self.description,
            "day_schedule": self.day_schedule.to_dict() if self.day_schedule else None,
            "location_changes": [lc.to_dict() for lc in self.location_changes] if self.location_changes else None,
            "home": self.home.to_dict() if self.home else None,
            "home_type": self.home_type,
            "owned_vehicles": [vehicle.to_dict() for vehicle in self.owned_vehicles],
            "travel_pass": self.travel_pass,
            "route_descriptions": self.route_descriptions,
            "journey_outcomes": [jo.to_dict() for jo in self.journey_outcomes] if self.journey_outcomes else None,
        }

    def to_json(self):
        return json.dumps(self.to_dict(), cls=NumpyEncoder, sort_keys=True)

    @classmethod
    def from_json(cls, json_str):
        if isinstance(json_str, str):
            data = json.loads(json_str)
        else:
            data = json_str
        agent = cls(id=data.get("id"))
        agent.seed = Seed.from_json(data["seed"]) if data.get("seed") else None
        agent.description = data.get("description")
        agent.day_schedule = DaySchedule.from_json(data["day_schedule"]) if data.get("day_schedule") else None
        agent.location_changes = [LocationChange.from_json(lc) for lc in data.get("location_changes", []) or []]
        agent.home = Building.from_json(data["home"]) if data.get("home") else None
        agent.home_type = data.get("home_type")
        agent.owned_vehicles = [
            Vehicle.from_dict(vehicle_data) if isinstance(vehicle_data, dict) else vehicle_data
            for vehicle_data in data.get("owned_vehicles", []) or []
        ]
        agent.route_descriptions = data.get("route_descriptions", None)
        agent.journey_outcomes = [JourneyOutcome.from_json(jo) for jo in data.get("journey_outcomes", []) or []]
        agent.travel_pass = data.get("travel_pass", None)

        return agent
