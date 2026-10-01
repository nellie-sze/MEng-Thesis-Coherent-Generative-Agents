from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Tuple

from module.action.action_module import ActionModule
from llm.huggingface_chat_api import HuggingfaceChatAPI
from model.agent import Agent
from model.day_schedule import DaySchedule
from model.location_change import LocationChange
from model.rule import (
    get_active_vehicles,
    update_linked_attribute,
)
from model.task import Task
from model.vehicle import VehicleRegistry
from module.planning.consistency_checker import check_inconsistencies
from module.planning.prompt.day_schedules import build_schedule_prompt
from module.planning.prompt.means_of_transport_selection import build_route_prompt
from module.planning.prompt.iterative_means_of_transport_selection import build_leg_prompt
from module.planning.prompt.decision_eval import build_eval_prompt
from util.json import extract_json_from
from util.list import split_list
from util.logging import log_error, log_debug, log_info
from util.time import time_to_seconds
from config.coherence_config import config

iterative = config['iterative']
force_regen = config['force_regen']
max_attempts = config['max_attempts']
print_prompts = config['print_prompts']
print_responses = config['print_responses']
class PlanningModule:
    last_regen_count = 0

    @staticmethod
    def apply_agent_state_update(agent: Agent, vehicle_registry: VehicleRegistry) -> None:
        for location_change in agent.location_changes or []:
            if not getattr(location_change, "decision", None):
                continue
            vehicle_name = location_change.decision.get("vehicle_name")
            if not vehicle_name:
                continue
            update_linked_attribute(agent, vehicle_registry.get(vehicle_name))

    @staticmethod
    def normalize_prompt_reference(prompt_reference: str, vehicle_registry: VehicleRegistry | None = None) -> str:
        normalized = prompt_reference.strip().lower()
        normalized = normalized.strip("<>[](){}\"'")
        if vehicle_registry is None:
            return normalized

        for vehicle in vehicle_registry.vehicles:
            if normalized == vehicle.prompt_reference.lower():
                return vehicle.prompt_reference
            if normalized == vehicle.name.lower():
                return vehicle.prompt_reference

        return normalized

    @staticmethod
    def coerce_decisions(raw_decisions, location_changes):
        if all(isinstance(decision, dict) for decision in raw_decisions):
            return raw_decisions

        coerced_decisions = []
        routable_location_changes = [lc for lc in location_changes if lc.possible_routes]
        for location_change, decision in zip(routable_location_changes, raw_decisions):
            if isinstance(decision, list) and len(decision) >= 2:
                coerced_decisions.append({
                    'route_id': str(location_change.route_id),
                    'reasoning': decision[0],
                    'means_of_transport': decision[1],
                })
            else:
                raise TypeError(f'Unsupported decision format: {decision!r}')
        return coerced_decisions

    @staticmethod
    def normalize_decision(decision, vehicle_registry: VehicleRegistry):
        prompt_reference = PlanningModule.normalize_prompt_reference(
            decision['means_of_transport'],
            vehicle_registry
        )
        vehicle = vehicle_registry.get_by_prompt(prompt_reference)
        decision['vehicle_name'] = vehicle.name
        decision['means_of_transport'] = vehicle.prompt_reference
        return decision

    @staticmethod
    def _print_prompts(title: str, prompts: list[str], agents: list[Agent] | None = None, show_all: bool = False) -> None:
        if not print_prompts or not prompts:
            return
        print(title)
        if show_all and agents is not None:
            for agent, prompt in zip(agents, prompts):
                print(f"Agent {agent.id} Prompt:\n{prompt}\n")
            return
        print(f"Agent 0: {prompts[0]}")

    @staticmethod
    def _format_schedule(day_schedule_data) -> str:
        return " | ".join(
            f"{task['time']} at {task['building_type']}: {task['action']}"
            for task in day_schedule_data
        )

    @staticmethod
    def _format_decisions(decisions) -> str:
        return " ".join(
            f"For {decision['route_id']} chose {decision.get('means_of_transport', 'unknown')} "
            f"because {decision.get('reasoning', 'no reasoning provided.')}"
            for decision in decisions
        )

    @staticmethod
    def generate_day_schedules_with_places_multithreaded(
        agents,
        building_options,
        max_workers,
        daily_context=None,
        vehicle_registry=None,
        previous_day_schedules=None,
    ):
        # Split the workload so each worker can generate day schedules independently.
        agents_per_worker = split_list(agents, max_workers)

        result_agents = []
        skipped_agents = []
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for worker_id in range(max_workers):
                future = executor.submit(
                    PlanningModule.generate_day_schedules_with_places,
                    agents_per_worker[worker_id],
                    building_options,
                    worker_id,
                    daily_context,
                    vehicle_registry,
                    previous_day_schedules,
                )
                futures.append(future)
            for future in as_completed(futures):
                try:
                    agents_with_day_schedule, agents_without_day_schedule = future.result()
                    result_agents.extend(agents_with_day_schedule)
                    skipped_agents.extend(agents_without_day_schedule)
                except Exception as e:
                    log_error(e)
                    log_error(f'[ERROR] Failed to execute task {future}')

        return result_agents, skipped_agents

    @staticmethod
    def generate_day_schedules_with_places(
        agents,
        building_options,
        worker_id,
        daily_context=None,
        vehicle_registry=None,
        previous_day_schedules=None,
    ):
        llm_api = HuggingfaceChatAPI(gpu_id=worker_id)
        day = daily_context.day if daily_context is not None and getattr(daily_context, "day", None) else None

        # Ask the model for structured daily tasks tied to allowed building categories.
        prompts = [build_schedule_prompt(
                    building_options,
                    agent.description,
                    agent.home_type,
                    daily_context,
                    previous_day_schedule=(
                        previous_day_schedules.get(agent.id)
                        if previous_day_schedules is not None else None
                    ),
                   )
                   for agent in agents]
        PlanningModule._print_prompts("DAY SCHEDULE PROMPTS:", prompts)
        responses = llm_api.get_completions(prompts)

        agents_with_day_schedule = []
        agents_without_day_schedule = []
        if print_responses: print("\nDAY SCHEDULE RESPONSES: ")
        for agent, response in zip(agents, responses):
            try:
                # Parse the generated JSON into the DaySchedule model.
                day_schedule_data = extract_json_from(response)['description_of_today']
                agent.day_schedule = DaySchedule.from_json({
                    'day': day,
                    'task_list': day_schedule_data
                })
                agents_with_day_schedule.append(agent)
                if print_responses:
                    compact_schedule = PlanningModule._format_schedule(day_schedule_data)
                    print(f"Agent {agent.id}: {compact_schedule}\n")
            except Exception as e:
                log_error(e)
                log_error(f'[ERROR] Failed to generate day schedule for {agent.to_json()}')
                log_error(f'[ERROR] Response:\n{response}')
                agents_without_day_schedule.append(agent)

        return agents_with_day_schedule, agents_without_day_schedule

    @staticmethod
    def extend_with_location_changes_multithreaded(agents: List[Agent],
                                                   max_workers,
                                                   traffic_sim,
                                                   daily_context=None) -> (List[Agent], List[Agent]): # type: ignore
        # Parallelise the location-change extraction across workers.
        agents_per_worker = split_list(agents, max_workers)

        agents = []
        skipped_agents = []
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for worker_id in range(max_workers):
                future = executor.submit(PlanningModule.extend_with_location_changes,
                                         agents_per_worker[worker_id],
                                         traffic_sim,
                                         daily_context)
                futures.append(future)
            for future in as_completed(futures):
                try:
                    agents_with_location_changes, agents_without_location_changes = future.result()
                    agents.extend(agents_with_location_changes)
                    skipped_agents.extend(agents_without_location_changes)
                except Exception as e:
                    log_error(e)

        return agents, skipped_agents

    @staticmethod
    def extend_with_location_changes(agents: List[Agent], traffic_sim, daily_context=None) -> (List[Agent], List[Agent]): # type: ignore
        agents_with_location_changes = []
        agents_without_location_changes = []

        for agent in agents:
            location_changes = PlanningModule.get_planned_location_changes(agent, traffic_sim, daily_context)
            agent.location_changes = location_changes
            if agent.location_changes:
                agents_with_location_changes.append(agent)
            else:
                agents_without_location_changes.append(agent)

        return agents_with_location_changes, agents_without_location_changes

    @staticmethod
    def get_planned_location_changes(agent: Agent, traffic_sim, daily_context=None) -> List[LocationChange]:
        # Anchor the agent to a concrete home before resolving the other activity locations.
        if not agent.home:
            agent.home = traffic_sim.get_random_apartment(agent.home_type)
        reference_location = agent.home.location
        tasks = agent.day_schedule.task_list

        building_types = set(task.building_type for task in tasks)
        buildings = {}
        # Pick one representative building for each building type used in the schedule.
        for building_type in building_types:
            try:
                building = ActionModule.get_building_with(agent, building_type, traffic_sim,
                                                          reference_location=reference_location, daily_context=daily_context)
                buildings[building_type] = building
                reference_location = building.location
            except Exception as e:
                log_error(e)

        location_change_task_tuples = PlanningModule.get_tasks_with_location_change(tasks)
        location_changes = []
        for index, task_tuple in enumerate(location_change_task_tuples):
            try:
                # Turn each cross-location task transition into a routed leg.
                route_id = ActionModule.generate_route_id(agent.id, index)
                from_task = task_tuple[0]
                from_building = buildings[from_task.building_type]
                to_task = task_tuple[1]
                to_building = buildings[to_task.building_type]

                if from_building.location == to_building.location:
                    raise Exception('Locations of both from and to building are the same!')

                location_change = LocationChange(
                    route_id=route_id,
                    from_task=from_task,
                    from_building=from_building,
                    to_task=to_task,
                    to_building=to_building
                )
                location_changes.append(location_change)
            except Exception as e:
                log_error(e)
        return location_changes

    @staticmethod
    def get_tasks_with_location_change(tasks: List[Task]) -> List[Tuple[Task, Task]]:
        location_change_tasks = []
        for i in range(len(tasks) - 1):
            if tasks[i].building_type != tasks[i + 1].building_type:
                location_change_tasks.append((tasks[i], tasks[i + 1]))
        return location_change_tasks

    @staticmethod
    def add_routes_multithreaded(agents: List[Agent], max_workers, active_config, actually_add_route_to_sim=False,
                                 use_geocoord=False, vehicle_registry=None, daily_context=None) -> List[Agent]:
        agents_per_worker = split_list(agents, max_workers)

        agents = []
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for worker_id in range(max_workers):
                future = executor.submit(PlanningModule.add_routes,
                                         agents_per_worker[worker_id],
                                         worker_id,
                                         active_config,
                                         actually_add_route_to_sim,
                                         use_geocoord,
                                         daily_context,
                                         vehicle_registry)
                futures.append(future)
            for future in as_completed(futures):
                try:
                    agents_with_routes = future.result()
                    agents.extend(agents_with_routes)
                except Exception as e:
                    log_error(e)

        return agents

    @staticmethod
    def add_routes(agents: List[Agent], worker_id, traffic_sim, actually_add_route_to_sim=False, use_geocoord=False, daily_context=None, vehicle_registry=None) -> \
            List[Agent]:
        try:
            PlanningModule.last_regen_count = 0
            local_traffic_sim = traffic_sim
            if isinstance(traffic_sim, dict):
                from module.action.closest_location_choice import ClosestLocationChoice
                from module.action.sumo.sumo_adapter import SumoAdapter

                urban_sampler = ClosestLocationChoice(
                    traffic_sim["buildings_file"],
                    traffic_sim["taz_file"],
                )
                local_traffic_sim = SumoAdapter(
                    urban_sampler,
                    traffic_sim["net_file"],
                    traffic_sim["poly_file"],
                    traffic_sim["v_types_file"],
                    traffic_sim["pt_stops_file"],
                    traffic_sim["pt_vehicles_file"],
                    traffic_sim,
                    vehicle_registry=vehicle_registry,
                )
            llm_api = HuggingfaceChatAPI(gpu_id=worker_id)
            # Gather the feasible routes before prompting the model to choose between them.
            agents = ActionModule.get_possible_routes_for_agents(agents, local_traffic_sim, use_geocoord=use_geocoord)
            log_debug(f'Possible-routes: total_legs={sum(len(a.location_changes) for a in agents)} legs_with_options={sum(1 for a in agents for lc in a.location_changes if lc.possible_routes)}')
            if iterative:
                agents = PlanningModule.get_iterative_route_decisions(agents, llm_api, vehicle_registry, daily_context=daily_context)
            else:
                agents = PlanningModule.get_route_decisions(agents, llm_api, vehicle_registry, daily_context=daily_context)
            agents = PlanningModule.set_sim_routes(agents, local_traffic_sim, actually_add_route_to_sim)
            return agents
        except Exception as e:
            log_error(e)
            return []
        finally:
            if isinstance(traffic_sim, dict) and 'local_traffic_sim' in locals():
                local_traffic_sim.stop_sim()

    @staticmethod
    def get_route_decisions(agents: List[Agent], llm_api, vehicle_registry: VehicleRegistry, daily_context=None) -> List[Agent]:
        # Build one prompt per agent covering all route legs in the day.
        prompts = [
            build_route_prompt(
                agent,
                vehicle_registry,
                daily_context=daily_context,
                active_vehicle_names=(
                    get_active_vehicles(agent, agent.location_changes[0], vehicle_registry)
                    if agent.location_changes else set()
                ),
            )
            for agent in agents
        ]
        PlanningModule._print_prompts('NON-ITERATIVE ROUTE DECISION PROMPTS:', prompts, agents, show_all=True)
        if not prompts:
            raise Exception('No routes available')
        results = llm_api.get_completions(prompts)
        
        if print_responses: print("\nROUTE DECISION RESPONSES: ")
        for agent, result in zip(agents, results):
            regen_reason = None
            for attempts in range(max_attempts):
                try:
                    # Attach the chosen transport mode back onto each routable leg.
                    decisions = PlanningModule.coerce_decisions(
                        extract_json_from(result)['decisions'],
                        agent.location_changes
                    )
                    readable_decisions = PlanningModule._format_decisions(decisions)
                    log_info(f"Agent {agent.id}: {readable_decisions}\n")
                    if print_responses:
                        print(f"Agent {agent.id}: {readable_decisions}\n")
                    decision_map = {decision['route_id']: decision for decision in decisions if 'route_id' in decision}
                    for location_change in agent.location_changes:
                        if location_change.possible_routes:
                            route_id = str(location_change.route_id)
                            if route_id not in decision_map:
                                raise Exception(f'No decision found for route_id: {route_id}')
                            location_change.decision = decision_map[route_id]
                            location_change.decision = PlanningModule.normalize_decision(
                                location_change.decision,
                                vehicle_registry
                            )
                except Exception as e:
                    log_error(f'Error: {e}\n\nNo decision found for result:\n{result}')

                if not force_regen:
                    break

                # Re-score the whole day plan and regenerate when the evaluator rejects it.
                regenerate, regen_reason = PlanningModule.evaluate_decision(
                    agent,
                    llm_api,
                    vehicle_registry,
                )
                log_info(f"Regen decision for agent {agent.id} on attempt {attempts}: regenerate={regenerate}, reason={regen_reason}")
                if not regenerate:
                    break

                PlanningModule.last_regen_count += 1
                for lc in agent.location_changes:
                    lc.decision = None
                #log_debug(f'Inconsistency detected for agent {agent.id}, regenerating decisions')

                if attempts < max_attempts - 1:
                    result = llm_api.get_completion(
                        build_route_prompt(
                            agent,
                            vehicle_registry,
                            regen_reason=regen_reason,
                            daily_context=daily_context,
                            active_vehicle_names=(
                                get_active_vehicles(agent, agent.location_changes[0], vehicle_registry)
                                if agent.location_changes else set()
                            ),
                        )
                    )
                if regen_reason and print_responses:
                    readable_decisions = PlanningModule._format_decisions(decisions)
                    print(f"Post-regeneration decisions for {agent.id}: {readable_decisions}\n")
            # Apply any agent-level state changes implied by the accepted decisions.
            PlanningModule.apply_agent_state_update(agent, vehicle_registry)
        log_debug(f'Decisions assigned: legs_with_decisions={sum(1 for a in agents for lc in a.location_changes if getattr(lc, "decision", None))}')
        return agents

    @staticmethod
    def evaluate_decision(agent: Agent, llm_api, vehicle_registry: VehicleRegistry) -> tuple[bool, str]:
        prompt = build_eval_prompt(agent, vehicle_registry)
        PlanningModule._print_prompts(f'DECISION EVAL PROMPTS for Agent {agent.id}:', [prompt], [agent], show_all=True)
        result = llm_api.get_completion(prompt)

        try:
            parsed = extract_json_from(result)
            regenerate = parsed.get("decision", "").strip().lower() == "yes"
            regen_reason = parsed.get("reason", "").strip()
        except Exception as e:
            log_error(f"Failed to parse decision eval result: {e}\nRaw result:\n{result}")
            regenerate = False
            regen_reason = ""
        if print_responses and regen_reason:
            print(f"Regeneration reason for agent {agent.id}: {regen_reason}\n")
        return regenerate, regen_reason

    @staticmethod
    def get_iterative_route_decisions(agents: List[Agent], llm_api, vehicle_registry: VehicleRegistry, daily_context=None) -> List[Agent]:
        if print_prompts:
            print(f'ITERATIVE ROUTE DECISION PROMPTS:')
        for agent in agents:
            attempts = 0
            regen_reason = None
            while attempts < max_attempts:
                all_previous_decisions = []
                # Rebuild the prompt leg by leg so later choices can see earlier ones.
                for location_change in agent.location_changes:
                    if location_change.possible_routes:
                        active_vehicle_names = get_active_vehicles(
                            agent,
                            location_change,
                            vehicle_registry,
                        )
                        prompt = build_leg_prompt(
                            agent,
                            location_change,
                            all_previous_decisions,
                            active_vehicle_names,
                            vehicle_registry,
                            daily_context=daily_context,
                            regen_reason=regen_reason,
                        )
                        PlanningModule._print_prompts(
                            f'ITERATIVE ROUTE DECISION PROMPTS for Agent {agent.id}:',
                            [prompt],
                        )
                        result = llm_api.get_completion(prompt)

                        try:
                            decision = PlanningModule.coerce_decisions(
                                extract_json_from(result)['decisions'],
                                [location_change]
                            )[0]
                            location_change.decision = PlanningModule.normalize_decision(decision, vehicle_registry)
                            # Keep the earlier choices for the next iterative prompt.
                            all_previous_decisions.append(location_change.decision)
                        except Exception as e:
                            log_error(f'Error: {e}\n\nNo decision found for result:\n{result}')

                attempts += 1
                if attempts < max_attempts and force_regen:
                    # Re-evaluate the assembled day plan before accepting it.
                    regenerate, regen_reason = PlanningModule.evaluate_decision(
                        agent,
                        llm_api,
                        vehicle_registry,
                    )
                    if regenerate:
                        PlanningModule.last_regen_count += 1
                        for lc in agent.location_changes:
                            lc.decision = None
                        log_debug(f'Inconsistency detected for agent {agent.id}, regenerating decisions')
                    else:
                        break
            # Apply any agent-level state changes implied by the accepted decisions.
            PlanningModule.apply_agent_state_update(agent, vehicle_registry)
        
        if print_responses:
            print("\nROUTE DECISION RESPONSES: ")
            for agent in agents:
                readable = PlanningModule._format_decisions([
                    {"route_id": lc.route_id, **lc.decision}
                    for lc in agent.location_changes
                    if lc.decision
                ])
                print(f"Agent {agent.id}: {readable}\n")

        log_debug(f'Decisions assigned: legs_with_decisions={sum(1 for a in agents for lc in a.location_changes if getattr(lc, "decision", None))}')
        return agents

    @staticmethod
    def set_sim_routes(agents: List[Agent], traffic_sim, actually_add_route_to_sim=False) -> List[Agent]:
        for agent in agents:
            routes = []
            for location_change in agent.location_changes:
                if location_change.decision:
                    route = None
                    try:
                        # Match the selected mode back to one of the precomputed route options.
                        decision = location_change.decision
                        possible_routes = location_change.possible_routes or []
                        vehicle_name = decision.get('vehicle_name')
                        if vehicle_name is None and 'means_of_transport' in decision:
                            normalized_prompt_reference = decision['means_of_transport'].strip().lower()
                            possible_route = next(
                                (
                                    route for route in possible_routes
                                    if route.vehicle.prompt_reference.lower() == normalized_prompt_reference
                                    or route.vehicle.name.lower() == normalized_prompt_reference
                                ),
                                None
                            )
                            vehicle_name = possible_route.vehicle.name if possible_route else None

                        if vehicle_name is None:
                            possible_route_summaries = [
                                {
                                    'vehicle_name': route.vehicle.name,
                                    'prompt_reference': route.vehicle.prompt_reference,
                                    'means_of_transport': route.means_of_transport,
                                    'route_id': location_change.route_id,
                                }
                                for route in possible_routes
                            ]
                            raise KeyError(
                                "vehicle_name could not be resolved from decision. "
                                f"decision={decision!r}, possible_routes={possible_route_summaries!r}"
                            )

                        possible_route = next((route for route in possible_routes
                                               if route.vehicle.name == vehicle_name), None)
                        if possible_route:
                            route = possible_route.to_dict()
                            route['route_id'] = decision['route_id']
                            route['means_of_transport'] = possible_route.vehicle.name
                            # Back-calculate a departure time that still reaches the target task on time.
                            route['departure_time'] = time_to_seconds(location_change.to_task.time) - route[
                                'travel_time']
                            if route['departure_time'] < 0:
                                route['departure_time'] = 0
                            if actually_add_route_to_sim:
                                route = traffic_sim.add_traffic_participant(route)
                            routes.append(route)
                    except Exception as e:
                        possible_route_summaries = [
                            {
                                'vehicle_name': possible_route.vehicle.name,
                                'prompt_reference': possible_route.vehicle.prompt_reference,
                                'means_of_transport': possible_route.means_of_transport,
                                'route': possible_route.route,
                                'travel_time': possible_route.travel_time,
                            }
                            for possible_route in (location_change.possible_routes or [])
                        ]
                        log_error(
                            f"{e}\n\n"
                            f"agent_id={agent.id}\n"
                            f"location_change_route_id={location_change.route_id}\n"
                            f"decision={location_change.decision!r}\n"
                            f"possible_routes={possible_route_summaries!r}\n"
                            f"resolved_route={route!r}"
                        )
            agent.route_descriptions = routes
        return agents
    
    @staticmethod
    def check_inconsistencies(agents: List[Agent], vehicle_registry: VehicleRegistry,
                              run_info=None, daily_context=None, csv_path=None, run_summary_csv_path=None,
                              reasoning_csv_path=None):
        return check_inconsistencies(
            agents,
            vehicle_registry,
            run_info=run_info,
            daily_context=daily_context,
            csv_path=csv_path,
            run_summary_csv_path=run_summary_csv_path,
            reasoning_csv_path=reasoning_csv_path,
        )

