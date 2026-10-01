from model.agent import Agent
from model.vehicle import VehicleRegistry
from module.planning.prompt.prompt_formatting import build_rule_text, format_owned_vehicles


def format_routes(location_change) -> str:
    if not location_change.possible_routes:
        return "No routes were available for this leg."

    route_lines = []
    for route in location_change.possible_routes:
        distance = route.distance_in_km() if route.distance is not None else "unknown distance"
        price = route.price_display()
        route_lines.append(
            f"- {route.vehicle.prompt_reference}: {route.travel_time_in_hhmm()} ({distance}, {price})"
        )
    return "\n".join(route_lines)


def format_plan(agent: Agent) -> str:
    decision_lines = []
    for location_change in agent.location_changes:
        if not location_change.possible_routes:
            continue

        chosen_mode = (
            location_change.decision.get("means_of_transport", "no decision recorded")
            if location_change.decision
            else "no decision recorded"
        )
        reasoning = (
            location_change.decision.get("reasoning", "No reasoning provided.")
            if location_change.decision
            else "No reasoning provided."
        )

        decision_lines.append(
            f"Leg {location_change.route_id}\n"
            f"From: {location_change.from_task.action} in {location_change.from_task.building_type} at {location_change.from_task.time}\n"
            f"To: {location_change.to_task.action} in {location_change.to_task.building_type} at {location_change.to_task.time}\n"
            f"Available modes:\n{format_routes(location_change)}\n"
            f"Chosen mode: {chosen_mode}\n"
            f"Model reasoning: {reasoning}"
        )

    return "\n\n".join(decision_lines)


def build_eval_prompt(agent: Agent, vehicle_registry: VehicleRegistry) -> str:
    rules = build_rule_text(vehicle_registry).strip()
    owned_vehicles = format_owned_vehicles(agent, vehicle_registry).strip()
    decisions = format_plan(agent)

    prompt = (
        "You are evaluating another language model's daily transport decisions for a single agent. "
        "Your task is to judge whether the mode of transport decisions are coherent and sensible under a set of criteria."
        "You are responsible for deciding whether or not the other language model should have to regenerate its decisions for this agent to produce a better response. "
        "\nThe criteria is as follows:"
        "The chosen transport modes must be reasonable given the agent's persona and context of the tasks they are travelling between. If they are not, you should choose to regenerate. "
        "For example, an elderly agent with a cane is unlikely to choose to bike, and a student carrying heavy groceries is unlikely to walk long distances. "
        "You should also evaluate whether the decisions are valid under the rules provided. If they violate any of the rules, then regeneration is needed. "
        "For example, certain large personal vehicles such as cars can only be taken from home and cannot be abandoned before returning home. " \
        "You should also choose to regenerate if an agent chose a mode of transport that is not one of the available options for a leg. "
        f"\nAgent persona:\n{agent.description}\n\n"
        f"Transport rules:\n{rules if rules else 'No transport rules were provided.'}\n\n"
        f"Owned vehicles:\n{owned_vehicles}\n\n"
        "Below is the full set of route decisions made by the other model. For each leg, you are given "
        "the available transport options and the transport mode chosen by the agent.\n\n"
        f"{decisions}\n\n"
        "Please evaluate the decisions. If any one decision doesn't make sense or is inconsistent with the rules/available "
        "mode choices shown for a leg, then you must choose 'yes' in your response.\n\n"
        f"Return exactly one compact JSON, no line breaks:\n"
        f"Your response must be in English.\n"
        '{"decision":"yes/no","reason":"One sentence explanation of why you believe the decisions should or should not be regenerated"}\n\n'
        'Use "yes" if the decisions need to be regenerated. '
        'Use "no" if the decisions are completely valid and do not need regeneration. '
        f"Now, your JSON response:"
    )

    return prompt.strip()
