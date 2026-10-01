from model.agent import Agent
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


def count_remaining(agent: Agent, location_change, vehicle_name: str) -> int:
    found_current = False
    remaining = 0
    for lc in agent.location_changes or []:
        if not found_current:
            if lc.route_id == location_change.route_id:
                found_current = True
            continue

        if not lc.possible_routes:
            continue

        if any(route.vehicle.name == vehicle_name for route in lc.possible_routes):
            remaining += 1
    return remaining


def format_leg_price(possible_route, agent: Agent, location_change,
                     active_vehicle_names: set[str]) -> str:
    activation_cost, leg_cost = possible_route.price if possible_route.price is not None else (0.0, 0.0)
    activation_cost = activation_cost or 0.0
    leg_cost = leg_cost or 0.0

    if activation_cost <= 0:
        return possible_route.price_display()

    already_activated = possible_route.vehicle.name in active_vehicle_names
    remaining_opportunities = count_remaining(
        agent,
        location_change,
        possible_route.vehicle.name,
    )
    remaining_text = f"; {remaining_opportunities} later chances" if remaining_opportunities > 0 else ""

    if already_activated:
        return "Free" if leg_cost == 0 else f"EUR {leg_cost:.2f}"

    if leg_cost == 0:
        return f"EUR {activation_cost:.2f} to start today{remaining_text}"
    return f"EUR {activation_cost:.2f} to start today + EUR {leg_cost:.2f} this leg{remaining_text}"

def build_leg_prompt(agent: Agent, location_change, prev_decisions,
                     active_vehicle_names: set[str],
                     vehicle_registry: VehicleRegistry, daily_context=None, regen_reason=None) -> str:
    few_shot = r"""
You live in Berlin and have several tasks to complete today, for which you need to plan
several trips. Berliners typically
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
    
    # Build previous decisions context (convert to human-readable strings first)
    vehicle_context = ""
    human_prev = []
    previous_decisions_str = "none yet"
    daily_context_text = ""

    if prev_decisions:
        human_prev = [decision['means_of_transport'] for decision in prev_decisions]
        previous_decisions_str = ", ".join(human_prev)

    vehicle_context = format_owned_vehicles(agent, vehicle_registry)
    previous_day_context = format_day_outcomes(agent)
    
    if not ablation:
        vehicle_context += f"\nFor your previous tasks today you made the following mode choices: {previous_decisions_str}\n"

    if guided_context and not ablation:
        vehicle_context += build_vehicle_context(
            agent,
            location_change,
            prev_decisions,
            active_vehicle_names,
            vehicle_registry,
        )

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
        
    task_context = f"Your previous task located at {location_change.from_task.building_type} was: {location_change.from_task.action} at {location_change.from_task.time}\n"
    task_context += f"You are travelling to {location_change.to_task.building_type} for: {location_change.to_task.action} at {location_change.to_task.time}\n"

    # Provide agent with route options
    route_choices = {
        'route_id': location_change.route_id,
        'from_task': location_change.from_task.to_dict(),
        'to_task': location_change.to_task.to_dict(),
        'possible_routes': [
            {
                'means_of_transport': possible_route.vehicle.prompt_reference,
                'travel_time_hh_mm': possible_route.travel_time_in_hhmm(),
                **({'distance': possible_route.distance_in_km()} if possible_route.distance is not None else {}),
                'price': format_leg_price(
                    possible_route,
                    agent,
                    location_change,
                    active_vehicle_names,
                ),
            }
            for possible_route in location_change.possible_routes
        ],
    }

    owned_vehicle_names = {vehicle.name for vehicle in agent.owned_vehicles}
    visible_routes = [
        route for route in route_choices['possible_routes']
        if not (
            hide_missing_personal_vehicles
            and vehicle_registry.get_by_prompt(route['means_of_transport']).ownable
            and vehicle_registry.get_by_prompt(route['means_of_transport']).name not in owned_vehicle_names
        )
    ]
    
    route_description = (
        f"For this leg you can choose only from the following {len(visible_routes)} "
        f"modes of transport: {[route['means_of_transport'] for route in visible_routes]}\n"
    )
    route_description += "The details of the available routes are:\n"
    for i, route in enumerate(visible_routes, 1):
        mot = route['means_of_transport']
        time = route['travel_time_hh_mm']
        dist = route.get('distance', 'unknown')
        price = route['price']
        route_description += f"{i}. {mot} - {time} ({dist}, {price})\n"

    available_mode_labels = "/".join([route['means_of_transport'] for route in visible_routes])
    json_template = f'{{"route_id":"{location_change.route_id}","means_of_transport":"<{available_mode_labels}>","reasoning":"a one sentence reasoning for your decision"}}'

    prompt = (
        f"You are:\n{agent.description}\n\n"
        f"{few_shot}\n"
        f"{rules}\n"
        f"{vehicle_context}\n"
        f"{previous_day_context}\n"
        f"{daily_context_text}\n"
        f"{regen_context}\n"
        f"{task_context}\n"
        f"{route_description}\n"
        f"For each leg, write one personal sentence explaining your choice, then pick the mode.\n\n"
        f"Return exactly one compact JSON, no line breaks:\n"
        f"Your response must be in English.\n"
        f"{{\"decisions\":[{json_template}]}}\n\n"
        f"Now, your JSON response:"
    )

    return prompt.strip()
