from model.building import Building
from model.task import Task
from util.time import seconds_to_hhmm, time_to_seconds


class JourneyOutcome:
    def __init__(
        self,
        route_id,
        from_task,
        from_building,
        to_task,
        to_building,
        decision=None,
        arrival_time=None,
        delay=None,
        crowding=0.0,
        time_loss=None,
    ):
        self.route_id = route_id
        self.from_task = from_task
        self.from_building = from_building
        self.to_task = to_task
        self.to_building = to_building
        self.decision = decision
        self.arrival_time = arrival_time
        self.delay = delay
        self.crowding = crowding
        self.time_loss = time_loss

    def _destination(self):
        if self.to_building and self.to_building.parameters:
            return self.to_building.parameters[0]
        if self.to_task and self.to_task.building_type:
            return self.to_task.building_type
        return "your destination"

    def _transport(self):
        if isinstance(self.decision, dict):
            return self.decision.get("means_of_transport") or "that mode of transport"
        return "that mode of transport"

    def _is_public(self):
        transport_label = self._transport().lower()
        return any(
            keyword in transport_label
            for keyword in [
                "public transport",
                "bus",
                "tram",
                "subway",
                "light rail",
                "train",
                "ferry",
            ]
        )

    @staticmethod
    def _minutes(value):
        return str(int(value))

    def describe_time_loss(self):
        if self.time_loss is None:
            return None

        elif self.time_loss < 0.4:
            return None
        elif self.time_loss < 0.7:
            return "moved slowly"
        else:
            return "moved very slowly"

    def describe_crowding(self):
        if self.crowding is None or not self._is_public():
            return None

        if self.crowding >= 0.9:
            return "felt very crowded"
        elif self.crowding >= 0.5:
            return "felt slightly crowded"
        else:
            return None

    def describe_delay(self):
        if self.delay is None:
            return None

        if -2 < self.delay < 2:
            return "arrived on time"
        elif self.delay <= -2:
            return f"arrived {self._minutes(abs(self.delay))} minutes early"
        else:
            return f"arrived {self._minutes(self.delay)} minutes late"

    def build_prompt_lines(self):
        time_loss_description = self.describe_time_loss()
        crowding_description = self.describe_crowding()
        delay_description = self.describe_delay()

        journey_descriptions = [
            description
            for description in [time_loss_description, crowding_description]
            if description
        ]

        if not journey_descriptions and not delay_description:
            return []

        intro = f"During {self._transport()} to {self._destination()}, "
        if journey_descriptions:
            sentence = intro
            if len(journey_descriptions) > 1:
                sentence += f"your journey {', '.join(journey_descriptions[:-1])}"
            else:
                sentence += f"your journey {journey_descriptions[0]}"
            if len(journey_descriptions) > 1:
                sentence += f" and {journey_descriptions[-1]}"
            if delay_description:
                sentence += f" and you {delay_description}"
            sentence += "."
            return [sentence]

        return [f"{intro}you {delay_description}."]

    def to_dict(self):
        target_arrival_seconds = time_to_seconds(self.to_task.time) if self.to_task and self.to_task.time else None
        return {
            "route_id": self.route_id,
            "from": {
                "task": self.from_task.to_dict(),
                "building": self.from_building.to_dict(),
            },
            "to": {
                "task": self.to_task.to_dict(),
                "building": self.to_building.to_dict(),
            },
            "decision": self.decision,
            "arrival_time": seconds_to_hhmm(self.arrival_time) if self.arrival_time is not None else None,
            "arrival_time_seconds": self.arrival_time,
            "scheduled_arrival_time": self.to_task.time if self.to_task else None,
            "scheduled_arrival_time_seconds": target_arrival_seconds,
            "delay": self.delay,
            "crowding": self.crowding,
            "timeLoss": self.time_loss,
        }

    @classmethod
    def from_json(cls, data):
        from_task = Task.from_json(data["from"]["task"])
        from_building = Building.from_json(data["from"]["building"])
        to_task = Task.from_json(data["to"]["task"])
        to_building = Building.from_json(data["to"]["building"])
        arrival_time = data.get("arrival_time_seconds")
        if arrival_time is None and data.get("arrival_time"):
            arrival_time = time_to_seconds(data["arrival_time"])
        return cls(
            route_id=data["route_id"],
            from_task=from_task,
            from_building=from_building,
            to_task=to_task,
            to_building=to_building,
            decision=data.get("decision"),
            arrival_time=arrival_time,
            delay=data.get("delay"),
            crowding=data.get("crowding", 0.0),
            time_loss=data.get("timeLoss"),
        )
