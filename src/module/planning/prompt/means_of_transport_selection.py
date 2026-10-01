from model.agent import Agent
from model.rule import get_active_vehicles
from model.vehicle import VehicleRegistry
from config.coherence_config import config
from module.planning.prompt.prompt_formatting import (
    build_rule_text,
    build_vehicle_context,
    format_owned_vehicles,
    format_day_outcomes,
)

ablation = config['ablation']
guided_context = config['guided_context']
hide_missing_personal_vehicles = config['hide_missing_personal_vehicles']


def format_route_price(possible_route, active_vehicle_names: set[str]) -> str:
    if possible_route.price is None:
        return "unknown"

    if isinstance(possible_route.price, (tuple, list)) and len(possible_route.price) == 2:
        activation_cost, leg_cost = possible_route.price
        activation_cost = activation_cost or 0.0
        leg_cost = leg_cost or 0.0

        if activation_cost > 0:
            if possible_route.vehicle.lifecycle.activation == "automatic":
                leg_cost_text = "Free" if leg_cost == 0 else f"EUR {leg_cost:.2f}"
                if active_vehicle_names and possible_route.vehicle.name in active_vehicle_names:
                    return leg_cost_text
                else:
                    return f"EUR {activation_cost:.2f} upfront and {leg_cost_text} for each use for the rest of the month"
            elif possible_route.vehicle.lifecycle.activation == "on_start":
                leg_cost_text = "Free" if leg_cost == 0 else f"EUR {leg_cost:.2f}"
                return f"EUR {activation_cost:.2f} upfront and {leg_cost_text} for each use for the rest of the day"

    return possible_route.price_display()

def build_route_prompt(agent: Agent, vehicle_registry: VehicleRegistry, regen_reason=None,
                       daily_context=None,
                       active_vehicle_names: set[str] | None = None) -> str:
    few_shot = r"""
You live in Berlin and have several tasks to complete today , for which you need to plan several trips . Berliners typically
- walk for very short trips ( <1 km ) ,
- bike for medium trips (1 -5 km ) ,
- use public transport for longer trips ( >5 km ) ,
- and drive only if they have a car immediately available .
The eight examples below are * shuffled *, but together they reflect a typical Berlin modal
split (2 walk , 1 bicycle , 3 car , 2 public transport ):
1. 0.3 km in 5 min -> ** walk ** ("300 m is fastest on foot ")
2. 4.0 km in 10 min -> ** car ** (" fastest for a 4 km morning commute ")
3. 8.0 km in 20 min -> ** public transportation ** (" subway is most reliable ")
4. 1.5 km in 7 min -> ** bicycle ** (" Berlin 's bike paths make this ideal ")
5. 0.7 km in 10 min ( with groceries ) -> ** walk ** (" easiest to carry bags ")
6. 5.0 km in 15 min -> ** public transportation ** (" smooth transfer on S - Bahn ")
7. 3.0 km in 12 min ( rainy ) -> ** car ** (" stay dry and faster than cycling ")
8. 12.0 km in 30 min -> ** car ** (" direct suburban route is best by car ")
"""

    rules = "You must always abide by the following rules when making your mode of transport choices: "
    prompt_rules = build_rule_text(vehicle_registry)
    if prompt_rules and not ablation:
        rules += prompt_rules
    else:
        rules = ""

    vehicle_context = format_owned_vehicles(agent, vehicle_registry)
    previous_day_context = format_day_outcomes(agent)
    active_vehicle_names = active_vehicle_names or set()
    daily_context_text = ""
    
    if guided_context and not ablation:
        if not active_vehicle_names and agent.location_changes:
            active_vehicle_names = get_active_vehicles(agent, agent.location_changes[0], vehicle_registry)
        vehicle_context += build_vehicle_context(
            agent,
            agent.location_changes[0],
            [],
            active_vehicle_names,
            vehicle_registry,
        ) if agent.location_changes else ""

    if daily_context is not None:
        if daily_context.environmental_context:
            daily_context_text += (
                f"{daily_context.environmental_context}. "
                f"If relevant to your agent or tasks, consider this when making your choices.\n"
            )

    if regen_reason:
        regen_context = f"Previously, when generating route decisions for this agent, you generated the following inconsistency: {regen_reason}" 
        regen_context += "Avoid repeating this inconsistency by making a different choice this time."
    else: regen_context = ""   

    formatted_routes = ""
    for lc in agent.location_changes:
        if lc.possible_routes:
            owned_vehicle_names = {vehicle.name for vehicle in agent.owned_vehicles}
            visible_routes = [
                route for route in lc.possible_routes
                if not (
                    hide_missing_personal_vehicles
                    and route.vehicle.ownable
                    and route.vehicle.name not in owned_vehicle_names
                )
            ]
            task_context = f"Your previous task located at {lc.from_task.building_type} was: {lc.from_task.action} at {lc.from_task.time}\n"
            task_context += f"You are travelling to {lc.to_task.building_type} for: {lc.to_task.action} at {lc.to_task.time}\n"

            #route_description = f"For this leg you can choose only from the following routes:"
            route_description = (
                f"For this leg you can choose only from the following {len(visible_routes)} "
                f"modes of transport: {[route.vehicle.prompt_reference for route in visible_routes]}\n"
            )
            route_description += "The details of the available routes are:\n"
            
            for i, route in enumerate(visible_routes, 1):
                mot = route.vehicle.prompt_reference
                time = route.travel_time_in_hhmm()
                dist = route.distance_in_km() if route.distance is not None else 'unknown'
                price = format_route_price(route, active_vehicle_names)
                route_description += f"{i}. {mot} - {time} ({dist}, {price})\n"

            formatted_routes += task_context + route_description + "\n"

    available_mode_labels = "/".join(vehicle.prompt_reference for vehicle in vehicle_registry.vehicles)
    json_template = ",".join(
        f'{{"route_id":"{lc.route_id}","means_of_transport":"<{available_mode_labels}>""reasoning":"a one sentence reasoning for your decision"}}'
        for lc in agent.location_changes
        if lc.possible_routes
    )
    
    prompt = (
        f"You are:\n{agent.description}\n"
        f"{few_shot}\n"
        f"{rules}\n"
        f"{vehicle_context}\n"
        f"{previous_day_context}\n"
        f"{daily_context_text}\n"
        f"{regen_context}\n"
        f"{formatted_routes}\n"
        f"For each leg, write one personal sentence explaining your choice, then pick the mode. Consider your choices for previous legs when making a new one. " 
        f"Return exactly one compact JSON, no line breaks:\n"
        f"Your response must be in English.\n"
        f"Important: every output `route_id` must exactly match the input `route_id`. Do not shorten, renumber, or invent IDs."
        f"{{\"decisions\":[{json_template}]}}\n\n"
        f"Now, your JSON response:"
    )

    return prompt.strip()
