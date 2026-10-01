import argparse
import csv
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json")
    return parser.parse_args()


def load_agents(input_path: Path) -> list[dict]:
    raw_agents = json.loads(input_path.read_text(encoding="utf-8"))
    agents = []
    for agent_data in raw_agents:
        if isinstance(agent_data, str):
            agent_data = json.loads(agent_data)
        agents.append(agent_data)
    return agents


def compute_rank(target_duration: float, all_durations: list[float]) -> int:
    return 1 + sum(duration < target_duration for duration in all_durations)


def compute_fastest_gap(target_duration: float, all_durations: list[float]) -> float:
    return seconds_to_minutes(target_duration - min(all_durations))


def compute_fastest_ratio(target_duration: float, all_durations: list[float]) -> float | None:
    fastest_duration = min(all_durations)
    if fastest_duration == 0:
        return None
    return target_duration / fastest_duration


def seconds_to_minutes(seconds: float | int | None) -> float | None:
    if seconds is None:
        return None
    return seconds / 60


def meters_to_km(meters: float | int | None) -> float | None:
    if meters is None:
        return None
    return meters / 1000


def build_rows(agents: list[dict]) -> list[dict]:
    rows = []

    for agent in agents:
        agent_id = agent.get("id")
        for location_change in agent.get("location_changes") or []:
            possible_routes = location_change.get("possible_routes") or []
            decision = location_change.get("decision") or {}
            all_durations = [
                route.get("travel_time")
                for route in possible_routes
                if route.get("travel_time") is not None
            ]

            if not all_durations:
                continue

            for route in possible_routes:
                vehicle = route.get("vehicle") or {}
                vehicle_name = (
                    vehicle.get("name")
                    or route.get("means_of_transport")
                    or ""
                )

                if vehicle_name != "air_taxi":
                    continue

                duration = route.get("travel_time")
                if duration is None:
                    continue

                chosen = "yes" if decision.get("vehicle_name") == "air_taxi" else "no"

                rows.append(
                    {
                        "agentID": agent_id,
                        "duration": seconds_to_minutes(duration),
                        "distance": meters_to_km(route.get("distance")),
                        "chosen": chosen,
                        "fastest_gap": compute_fastest_gap(duration, all_durations),
                        "fastest_ratio": compute_fastest_ratio(duration, all_durations),
                        "ranking": compute_rank(duration, all_durations),
                    }
                )

    return rows


def write_csv(output_path: Path, rows: list[dict]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "agentID",
                "duration",
                "distance",
                "chosen",
                "fastest_gap",
                "fastest_ratio",
                "ranking",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    input_path = Path(args.input_json).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_path = Path.cwd() / "air_taxi_options.csv"

    agents = load_agents(input_path)
    rows = build_rows(agents)
    write_csv(output_path, rows)

    print(f"Wrote {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
