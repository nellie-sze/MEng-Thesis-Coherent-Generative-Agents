from __future__ import annotations

import os

from config.coherence_config import config as coherence_config
from config.sim_config import config_wedding_sumo as default_config
from config.vehicle_config import vehicle_configs
from model.agent import Agent
from model.vehicle import VehicleRegistry
from module.action.closest_location_choice import ClosestLocationChoice
from module.action.sumo.sumo_adapter import SumoAdapter
from module.planning.day_metrics import build_rows, write_csv
from module.planning.planning_module import PlanningModule
from module.profile.profile_module import ProfileModule
from module.profile.seed.mid_b1_seed_generator import SeedGeneratorMiD
from util.logging import log_info
from util.storage import Storage
from util.time import Timer
from util.trips import generate_trips_xml

DESCRIPTION_POSTFIX = "1_description"
NO_DESCRIPTION_POSTFIX = "1_no_description"
DAY_SCHEDULE_POSTFIX = "2_day_schedule"
NO_DAY_SCHEDULE_POSTFIX = "2_no_day_schedule"
LOCATION_CHANGES_POSTFIX = "3_location_changes"
NO_LOCATION_CHANGES_POSTFIX = "3_no_location_changes"
ROUTES_POSTFIX = "4_route_descriptions"


def _count_air_routes(agents: list[Agent]) -> int:
    return sum(
        1
        for agent in agents
        for location_change in (agent.location_changes or [])
        if any(route.vehicle.name == "air_taxi" for route in (location_change.possible_routes or []))
    )


def _count_air_picks(agents: list[Agent]) -> int:
    return sum(
        1
        for agent in agents
        for location_change in (agent.location_changes or [])
        if (location_change.decision or {}).get("vehicle_name") == "air_taxi"
    )

def _resolve_storage_path(storage_path_override: str | None) -> str:
    return storage_path_override or default_config["storage_path"]


def _build_traffic_sim(storage_path: str, config: dict, vehicle_registry: VehicleRegistry) -> tuple[Storage, SumoAdapter, SeedGeneratorMiD]:
    storage = Storage(storage_path)

    urban_sampler = ClosestLocationChoice(config["buildings_file"], config["taz_file"])
    traffic_sim = SumoAdapter(
        urban_sampler,
        config["net_file"],
        config["poly_file"],
        config["v_types_file"],
        config["pt_stops_file"],
        config["pt_vehicles_file"],
        config,
        vehicle_registry=vehicle_registry,
    )
    seed_generator = SeedGeneratorMiD(config["census_paths"])
    return storage, traffic_sim, seed_generator


def _load_preloaded_agents(storage: Storage, preloaded_agents_path: str | None) -> list[Agent] | None:
    if preloaded_agents_path is None:
        return None

    return storage.get_agents(preloaded_agents_path)


def _generate_initial_agents(
    seed_generator: SeedGeneratorMiD,
    num_agents: int,
    max_workers: int,
    exclude_too_young: bool,
    exclude_too_old: bool,
    vehicle_registry: VehicleRegistry,
) -> tuple[list[Agent], list[Agent]]:
    seeded_agents = ProfileModule.generate_seeded_agents(seed_generator, num_agents, vehicle_registry)
    return ProfileModule.build_profiles_batch(
        seeded_agents,
        max_workers,
        exclude_too_young,
        exclude_too_old,
        vehicle_registry,
    )


def _merge_agents_by_id(*agent_groups: list[Agent]) -> list[Agent]:
    merged_agents: dict[int, Agent] = {}
    for agent_group in agent_groups:
        for agent in agent_group:
            merged_agents[agent.id] = agent
    return [merged_agents[agent_id] for agent_id in sorted(merged_agents)]


def run(
    storage_path_override: str | None = None,
    preloaded_agents_path: str | None = None,
    day_index: int | None = None,
    config: dict | None = None,
    daily_context=None,
    vehicle_registry: VehicleRegistry | None = None,
    full_vehicle_registry: VehicleRegistry | None = None,
    previous_day_schedules: dict[int, object] | None = None,
) -> list[Agent]:
    # Resolve the run-specific inputs before initialising the pipeline.
    active_config = config or default_config
    storage_path = _resolve_storage_path(storage_path_override)
    day = daily_context.day if daily_context is not None and getattr(daily_context, "day", None) else None

    log_info("Starting traffic simulacra")
    timer = Timer()
    timer.start()

    log_info("Initialising parameter, necessary objects...")
    #log_info(f"Config used:\n{active_config}")
    #log_info(f"Active storage path: {storage_path}")
    if daily_context is not None:
        log_info(f"Validated daily context received for {day}: {daily_context.to_dict()}")

    max_workers = active_config["workers"]
    num_agents = active_config["num_agents"]
    exclude_too_young = active_config["exclude_too_young"]
    exclude_too_old = active_config["exclude_too_old"]
    active_vehicle_registry = vehicle_registry or VehicleRegistry.from_configs(vehicle_configs)
    validation_vehicle_registry = full_vehicle_registry or active_vehicle_registry

    storage, traffic_sim, seed_generator = _build_traffic_sim(storage_path, active_config, active_vehicle_registry)
    log_info("Finished storage and SUMO adapter initialisation")

    try:
        # Start from a stored snapshot when one is provided, otherwise sample fresh agents.
        preloaded_agents = _load_preloaded_agents(storage, preloaded_agents_path)

        if preloaded_agents is None:
            log_info("Initialising agents and enriching them with descriptions...")
            agents_with_description, agents_without_description = _generate_initial_agents(
                seed_generator,
                num_agents,
                max_workers,
                exclude_too_young,
                exclude_too_old,
                active_vehicle_registry,
            )
        else:
            agents_with_description = preloaded_agents
            agents_without_description = []
            log_info(f"Loaded {len(preloaded_agents)} preloaded agents from {preloaded_agents_path}")

        description_agents_path = storage.write_agents(agents_with_description, DESCRIPTION_POSTFIX)
        storage.write_agents(agents_without_description, NO_DESCRIPTION_POSTFIX)
        log_info(f"[DESCRIPTION] {len(agents_with_description)} described agents.")
        log_info(f"[DESCRIPTION] {len(agents_without_description)} agents without description.")
        carried_agents = list(agents_with_description)

        # Generate a day schedule for each described agent.
        log_info("Adding day schedules with the respective places to the agents...")
        building_options = traffic_sim.get_building_categories_string()
        agents_with_day_schedule, agents_without_day_schedule = PlanningModule.generate_day_schedules_with_places_multithreaded(
            carried_agents,
            building_options,
            max_workers,
            daily_context,
            active_vehicle_registry,
            previous_day_schedules,
        )

        storage.write_agents(agents_with_day_schedule, DAY_SCHEDULE_POSTFIX)
        storage.write_agents(agents_without_day_schedule, NO_DAY_SCHEDULE_POSTFIX)
        log_info(f"[DAY_SCHEDULE] {len(agents_with_day_schedule)} agents with day schedule.")
        log_info(f"[DAY_SCHEDULE] {len(agents_without_day_schedule)} agents without day schedule.")

        # Convert daily tasks into actual place-to-place movements.
        log_info("Extracting location changes of agents...")
        agents_with_location_changes, agents_without_location_changes = PlanningModule.extend_with_location_changes_multithreaded(
            agents_with_day_schedule,
            max_workers,
            traffic_sim,
            daily_context,
        )

        storage.write_agents(agents_with_location_changes, LOCATION_CHANGES_POSTFIX)
        storage.write_agents(agents_without_location_changes, NO_LOCATION_CHANGES_POSTFIX)
        log_info(f"[LOCATION_CHANGES] {len(agents_with_location_changes)} agents with location changes.")
        log_info(f"[LOCATION_CHANGES] {len(agents_without_location_changes)} agents without location changes.")

        # Build route choices and let the planner select one for each leg.
        log_info("Adding routes...")
        routed_agents = PlanningModule.add_routes_multithreaded(
            agents_with_location_changes,
            max_workers,
            active_config,
            daily_context=daily_context,
            vehicle_registry=active_vehicle_registry,
        )

        # Fallback for saving agents if route generation fails
        if agents_with_location_changes and not routed_agents:
            agents_without_location_changes.extend(agents_with_location_changes)
        else:
            storage.write_agents(routed_agents, ROUTES_POSTFIX)

        final_agents = _merge_agents_by_id(
            agents_without_day_schedule,
            agents_without_location_changes,
            routed_agents,
        )
        
        created_route_description_count = sum(len(agent.route_descriptions or []) for agent in routed_agents)
        total_route_descriptions_count = sum(
            sum(
                1
                for index in range(len(agent.day_schedule.task_list) - 1)
                if agent.day_schedule.task_list[index].building_type
                != agent.day_schedule.task_list[index + 1].building_type
            )
            for agent in agents_with_location_changes
        )
        log_info(f"[ROUTES] {created_route_description_count}/{total_route_descriptions_count} routes created.")

        # Run the final rule-based consistency sweep over the completed agents and save in CSV.
        run_info = {
            "run_name": os.path.basename(storage_path.rstrip("/\\")),
            "model": coherence_config["model"],
            "ablation": coherence_config["ablation"],
            "iterative": coherence_config["iterative"],
            "force_regen": coherence_config["force_regen"],
            "guided_context": coherence_config["guided_context"],
            "regen_decision_count": PlanningModule.last_regen_count,
            "air_taxi_suggested_count": _count_air_routes(routed_agents),
            "air_taxi_chosen_count": _count_air_picks(routed_agents),
        }

        inconsistent_agents = PlanningModule.check_inconsistencies(
            routed_agents,
            validation_vehicle_registry,
            run_info=run_info,
            daily_context=daily_context,
            csv_path=f"{storage_path}/inconsistencies.csv",
            run_summary_csv_path=f"{storage_path}/run_metrics.csv",
            reasoning_csv_path=f"{storage_path}/agent_reasoning.csv",
        )
        start_of_day_agents = storage.get_agents(description_agents_path)
        write_csv(
            build_rows(
                start_of_day_agents=start_of_day_agents,
                agents=final_agents,
                vehicle_registry=active_vehicle_registry,
                daily_context=daily_context,
                day_index=day_index or 1,
            ),
            f"{storage_path}/day_metrics.csv",
        )

        log_info(f"[ROUTES] {len(inconsistent_agents)}/{len(routed_agents)} agents with routing inconsistencies detected.\n")
        log_info(f"[INFO] {day} finished with {len(routed_agents)} routed agents and {len(final_agents)} total agents.")

        # Export the finished route descriptions into trips.xml for simulation.
        log_info("Extracting trips.xml...")
        route_descriptions = [
            {"agent_id": agent.id, **route_description}
            for agent in final_agents
            for route_description in (agent.route_descriptions or [])
        ]
        trips_xml, uam_trips_xml = generate_trips_xml(route_descriptions, config["net_file"])
        storage.write_trips(trips_xml)
        storage.write_uam_trips(uam_trips_xml)

        log_info(f"[TIME] Total runtime: {timer.stop()}.")
        return final_agents
    finally:
        traffic_sim.stop_sim()
        log_info("Finished traffic simulation.\n\n")


def main(
    storage_path_override: str | None = None,
    preloaded_agents_path: str | None = None,
    day_index: int | None = None,
    daily_context=None,
    vehicle_registry: VehicleRegistry | None = None,
    full_vehicle_registry: VehicleRegistry | None = None,
    previous_day_schedules: dict[int, object] | None = None,
) -> list[Agent]:
    return run(
        storage_path_override=storage_path_override,
        preloaded_agents_path=preloaded_agents_path,
        day_index=day_index,
        daily_context=daily_context,
        vehicle_registry=vehicle_registry,
        full_vehicle_registry=full_vehicle_registry,
        previous_day_schedules=previous_day_schedules,
    )


if __name__ == "__main__":
    main()
