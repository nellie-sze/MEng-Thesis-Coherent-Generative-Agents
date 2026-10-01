import json
from typing import List

from model.agent import Agent
from model.building import Building
from model.possible_route import PossibleRoute
from util.logging import log_error
from util.time import time_to_seconds


class ActionModule:
    @staticmethod
    def _price_route(possible_route: PossibleRoute) -> PossibleRoute:
        cost_function = possible_route.vehicle.cost_function()
        possible_route.price = cost_function(possible_route.travel_time, possible_route.distance)
        return possible_route

    @staticmethod
    def get_possible_routes_for_agents(agents: List[Agent], traffic_sim, use_geocoord=False) -> List[Agent]:
        for agent in agents:
            for index, location_change in enumerate(agent.location_changes):
                try:
                    # Resolve the route options for each leg before the planner sees the choices.
                    from_location = location_change.from_building.get_location(geo=use_geocoord)
                    to_location = location_change.to_building.get_location(geo=use_geocoord)
                    arrival_time = time_to_seconds(location_change.to_task.time)
                    possible_routes = ActionModule.get_possible_routes(from_location,
                                                                       to_location,
                                                                       arrival_time,
                                                                       traffic_sim)
                    agent.location_changes[index].possible_routes = possible_routes
                except Exception as e:
                    log_error(f'{e}\n\n'
                              f'from_location:{from_location}\n'
                              f'to_location:{to_location}\n\n'
                              f'location change:{json.dumps(agent.location_changes[index].to_dict(), indent=4)}\n\n'
                              f'agent{json.dumps(agent.to_json(), indent=4)}\n\n')
        return agents

    @staticmethod
    def find_routes(from_location, to_location, arrival_time, traffic_sim) -> List[PossibleRoute]:
        possible_routes = []
        # Probe the routing backend once for every registered vehicle type.
        for vehicle in traffic_sim.vehicle_registry.vehicles:
            route = traffic_sim.branch_by_execution(
                vehicle,
                from_location,
                to_location,
                arrival_time,
            )
            if route and route.route:
                possible_routes.append(ActionModule._price_route(route))

        if not possible_routes:
            raise Exception('No route found!')

        return possible_routes

    @staticmethod
    def get_building_with(agent: Agent, building_type, traffic_sim, reference_location=None, daily_context=None) -> Building:
        if reference_location is None:
            reference_location = agent.home.location

        if building_type == agent.home_type:
            return agent.home

        event_location_id = next(
            (event.location_id for event in (daily_context.events if daily_context else []) if event.name == building_type),
            None,
        )
        if event_location_id is not None:
            return traffic_sim.get_building_by_id(event_location_id, building_type)

        return traffic_sim.get_building_with(reference_location, building_type)


    @staticmethod
    def generate_route_id(agent_id, index):
        return int(f"{agent_id}{index:02d}{index + 1:02d}")
