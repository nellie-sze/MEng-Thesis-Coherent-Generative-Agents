import json
import os
import shutil
import tempfile
from pathlib import Path

from model.agent import Agent
from util.file import write_file, read_file, create_folders


class Storage:
    def __init__(self, storage_path):
        self.storage_path = storage_path
        create_folders(self.storage_path)

        self.trips_xml_path = f'{storage_path}/trips.xml'
        self.uam_trips_xml_path = f'{storage_path}/uam_trips.xml'

        self.agents_file = 'agents.json'

    def write_agents(self, agents, postfix):
        agents_str = json.dumps([agent.to_json() for agent in agents])
        agents_file_path = f'{self.storage_path}/agents_{postfix}.json'
        write_file(agents_file_path, agents_str)
        return agents_file_path

    def get_agents(self, agents_file_path):
        agents_json = read_file(agents_file_path)

        agents_data = json.loads(agents_json)

        return [Agent.from_json(agent_data) for agent_data in agents_data]

    def write_trips(self, trips_xml):
        write_file(self.trips_xml_path, trips_xml)

    def write_uam_trips(self, trips_xml):
        write_file(self.uam_trips_xml_path, trips_xml)


def existing_agents_path(storage_path: str | Path, postfix: str) -> str | None:
    agents_path = Path(storage_path) / f"agents_{postfix}.json"
    if agents_path.exists():
        return str(agents_path)
    return None


def write_day_descriptions(day_storage_path: str | Path, agents, postfix: str) -> str:
    storage = Storage(str(day_storage_path))
    return storage.write_agents(agents, postfix)


def backup_agents_file(parent_storage_path: str | Path, source_agents_path: str | Path, postfix: str) -> Path:
    parent_storage_path = Path(parent_storage_path)
    source_agents_path = Path(source_agents_path)
    create_folders(str(parent_storage_path))
    backup_path = parent_storage_path / f".agents_{postfix}.backup.json"

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=parent_storage_path,
        prefix=f"agents_{postfix}_backup_",
        suffix=".json",
    ) as handle:
        temp_backup_path = Path(handle.name)
        handle.write(source_agents_path.read_text(encoding="utf-8"))

    os.replace(temp_backup_path, backup_path)
    return backup_path


def prepare_day_directories(
    parent_storage_path: str | Path,
    description_postfix: str,
    preserve_day1_descriptions: bool,
) -> None:
    parent_storage_path = Path(parent_storage_path)
    description_filename = f"agents_{description_postfix}.json"
    backup_path: Path | None = None
    day1_description_path = parent_storage_path / "day1" / description_filename

    if preserve_day1_descriptions and day1_description_path.exists():
        backup_path = backup_agents_file(parent_storage_path, day1_description_path, description_postfix)

    try:
        for child in parent_storage_path.iterdir():
            if child.is_dir() and child.name.startswith("day"):
                shutil.rmtree(child)
    except Exception:
        raise

    if backup_path is not None and backup_path.exists():
        restored_day1_path = parent_storage_path / "day1"
        restored_description_path = restored_day1_path / description_filename
        create_folders(str(restored_day1_path))
        restored_description_path.write_text(backup_path.read_text(encoding="utf-8"), encoding="utf-8")
        backup_path.unlink()
