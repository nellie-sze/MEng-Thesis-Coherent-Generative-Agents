import json

from model.task import Task


class DaySchedule:
    def __init__(self, day, task_list):
        self.day = day
        self.task_list = task_list

    def to_dict(self):
        task_list_dict = [task.to_dict() for task in self.task_list]
        return {
            'day': self.day,
            'task_list': task_list_dict
        }

    def to_json(self):
        return json.dumps(self.to_dict())

    def to_compact_summary(self) -> str:
        if not self.task_list:
            return "No tasks were scheduled."

        task_summaries = [
            f"{task.time} at {task.building_type}: {task.action}"
            for task in self.task_list
        ]
        day_prefix = f"On {self.day}, " if self.day else ""
        return day_prefix + " | ".join(task_summaries)

    @classmethod
    def from_json(cls, json_str):
        if isinstance(json_str, str):
            data = json.loads(json_str)
        else:
            data = json_str
        return cls(day=data.get('day'), task_list=[Task.from_json(task) for task in data.get('task_list', [])])
