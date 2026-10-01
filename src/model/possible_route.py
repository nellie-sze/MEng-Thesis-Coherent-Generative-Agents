from util.time import seconds_to_hhmm
from model.vehicle import Vehicle


class PossibleRoute:
    def __init__(self, vehicle, route, travel_time, distance=None, price=None, details=None):
        self.vehicle = vehicle if isinstance(vehicle, Vehicle) else Vehicle.from_dict(vehicle)
        self.route = route
        self.travel_time = travel_time
        self.distance = distance
        self.price = price
        self.details = details or {}

    @property
    def means_of_transport(self):
        return self.vehicle.name

    def to_dict(self):
        return {
            "vehicle": self.vehicle.to_dict(),
            "means_of_transport": self.means_of_transport,
            "route": self.route,
            "travel_time": self.travel_time,
            "distance": self.distance,
            "price": self.price,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data):
        vehicle = data.get("vehicle")
        if vehicle is None:
            vehicle = {
                "name": data["means_of_transport"],
                "prompt_reference": data["means_of_transport"],
                "SUMO_typeID": data.get("SUMO_typeID", ""),
                "execution_kind": data.get("execution_kind", "vehicle"),
            }
        return cls(
            vehicle=vehicle,
            route=data["route"],
            travel_time=data["travel_time"],
            distance=data.get("distance"),
            price=data.get("price"),
            details=data.get("details"),
        )

    def travel_time_in_hhmm(self):
        return seconds_to_hhmm(self.travel_time)

    def distance_in_km(self):
        if self.distance is None:
            return None
        distance = round(int(self.distance) / 1000, 2)
        return f'{distance}km'

    def price_display(self):
        if self.price is None:
            return "unknown"
        if isinstance(self.price, (tuple, list)) and len(self.price) == 2:
            activation_cost, leg_cost = self.price
            activation_cost = activation_cost or 0.0
            leg_cost = leg_cost or 0.0

            if activation_cost == 0 and leg_cost == 0:
                return "Free"
            if activation_cost > 0 and leg_cost == 0:
                return f"EUR {activation_cost:.2f}/day"
            if activation_cost > 0 and leg_cost > 0:
                return f"EUR {activation_cost:.2f} to start + EUR {leg_cost:.2f} this leg"
            return f"EUR {leg_cost:.2f}"
        if self.price == 0:
            return "Free"
        return f"EUR {self.price:.2f}"
