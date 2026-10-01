import random
from concurrent.futures import ProcessPoolExecutor, as_completed
from llm.huggingface_chat_api import HuggingfaceChatAPI
from model.agent import Agent
from module.profile.prompt.description import build_profile_prompt
from util.json import extract_json_from
from util.list import split_list
from util.logging import log_error, log_debug, log_warning
from config.coherence_config import config

print_prompts = config['print_prompts']
print_responses = config['print_responses']

class ProfileModule:
    @staticmethod
    def seed_value(agent, key, default=None):
        if key in agent.seed.attributes:
            return agent.seed.attributes[key]
        if key in agent.seed.additional_data:
            return agent.seed.additional_data[key]
        lower_key = str(key).lower()
        for source in (agent.seed.attributes, agent.seed.additional_data):
            for source_key, value in source.items():
                if str(source_key).lower() == lower_key:
                    return value
        return default

    @staticmethod
    def coerce_numeric(value, default=0.0):
        if value in (None, ''):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        normalized = str(value).strip().replace(',', '.')
        try:
            return float(normalized)
        except ValueError:
            return default

    @staticmethod
    def normalize_home_type(home_type):
        if not home_type:
            return 'apartments'

        normalized = home_type.strip().lower()
        aliases = {
            'apartment': 'apartments',
            'apartments': 'apartments',
            'flat': 'apartments',
            'flats': 'apartments',
            'residential': 'residential',
            'residence': 'residential',
            'student_accomodation': 'student_accomodation',
            'student accomodation': 'student_accomodation',
            'home': 'house',
            'house': 'house',
            'detached': 'detached',
            'dorms': 'dormitory',
            'dormitories': 'dormitory',
            'dormitory': 'dormitory',
        }

        mapped = aliases.get(normalized, normalized)
        if mapped not in ['student_accomodation', 'house', 'apartments', 'dormitory', 'detached', 'residential']:
            log_warning(f"Unknown home_type {home_type!r}; falling back to 'apartments'")
            return 'apartments'
        return mapped
    
    @staticmethod
    def generate_vehicle_ownership(agent, vehicle_registry=None):
        for vehicle in vehicle_registry.ownable:
            ownership_probability = vehicle.ownership_probability if vehicle.ownership_probability else {'type': 'fixed', 'value': 0.0}
            if ownership_probability['type'] == 'fixed':
                prob = ProfileModule.coerce_numeric(ownership_probability.get('value'), default=0.0)

            elif ownership_probability['type'] == 'ratio':
                attr1 = ownership_probability['numerator']
                attr2 = ownership_probability['denominator'] 
                numerator_value = ProfileModule.seed_value(agent, attr1)
                denominator_value = ProfileModule.seed_value(agent, attr2)
                try:
                    numerator = int(numerator_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Could not parse {attr1}={numerator_value!r} as an integer for vehicle '{vehicle.name}'."
                    ) from exc
                try:
                    denominator = int(denominator_value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"Could not parse {attr2}={denominator_value!r} as an integer for vehicle '{vehicle.name}'."
                    ) from exc
                prob = numerator / denominator if denominator else 0.0
            else:
                prob = 0.0
            if random.uniform(0, 1) < prob:
                log_debug(f"Agent {agent.id} owns vehicle: {vehicle.name}")
                agent.owned_vehicles.append(vehicle)
        return agent
    
    def generate_ticket_ownership(agent):
        ticket_value = ProfileModule.seed_value(agent, 'Ticket Type')
        transport_passes = ['Weekly ticket, monthly ticket without subscription',
                            'Monthly pass by subscription, annual pass (environmental pass etc.)',
                            'Job ticket, semester ticket etc. (company subscription, student ticket)']
        if ticket_value in transport_passes:
            agent.travel_pass = True
            log_debug(f"Agent {agent.id} has a travel pass")
        else: agent.travel_pass = False
        return agent

    @staticmethod
    def generate_seeded_agents(seed_generator, num_agents, vehicle_registry=None):
        agents = []
        seeds = seed_generator.generate_seeds(num_agents)
        for count, seed in enumerate(seeds):
            agent = Agent(count)
            agent.seed = seed
            # Attach deterministic ownership state before the LLM-generated persona is added.
            agent = ProfileModule.generate_vehicle_ownership(agent, vehicle_registry)
            agent = ProfileModule.generate_ticket_ownership(agent)
            agents.append(agent)
        return agents

    @staticmethod
    def _log_first_prompt(prompts):
        if print_prompts and prompts:
            print(f"Agent 0: {prompts[0]}")

    @staticmethod
    def _log_profile(agent):
        if print_responses:
            print(f"Agent {agent.id}: Home_type: {agent.home_type}\nDescription: {agent.description}\n")

    @staticmethod
    def build_profiles_batch(agents, max_workers, exclude_too_young, exclude_too_old, vehicle_registry=None):
        # Split the sampled agents so each worker can generate personas in parallel.
        agents_per_worker = split_list(agents, max_workers)

        result_agents = []
        agents_without_description = []
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for worker_id in range(max_workers):
                future = executor.submit(
                    ProfileModule.build_profiles,
                    agents_per_worker[worker_id],
                    worker_id,
                    exclude_too_young,
                    exclude_too_old,
                    vehicle_registry
                )
                futures.append(future)
            for future in as_completed(futures):
                try:
                    described_agents, skipped_agents = future.result()
                    result_agents.extend(described_agents)
                    agents_without_description.extend(skipped_agents)
                except Exception as e:
                    log_error(e)
                    log_error(f'[ERROR] Failed to execute task {future}')

        return result_agents, agents_without_description

    @staticmethod
    def build_profiles(agents, worker_id, exclude_too_young=True, exclude_too_old=True, vehicle_registry=None):
        llm_api = HuggingfaceChatAPI(gpu_id=worker_id)

        agents_to_be_described = []
        skipped_agents = []
        # Filter seeds before prompting so excluded age groups never reach description generation.
        for agent in agents:
            if exclude_too_young and agent.seed.too_young():
                skipped_agents.append(agent)
            elif exclude_too_old and agent.seed.too_old():
                skipped_agents.append(agent)
            else:
                agents_to_be_described.append(agent)

        # Generate one persona prompt per remaining agent seed.
        prompts = [build_profile_prompt(agent.seed) for agent in agents_to_be_described]
        ProfileModule._log_first_prompt(prompts)
        responses = llm_api.get_completions(prompts)
        described_agents = []
        failed_agents = []
        for agent, response in zip(agents_to_be_described, responses):
            try:
                # Persist the generated persona text and inferred home type on the agent.
                response_json = extract_json_from(response)
                description = response_json['persona_description']
                home_type = ProfileModule.normalize_home_type(response_json.get('home_type'))
                agent.description = description
                agent.home_type = home_type
                described_agents.append(agent)
                ProfileModule._log_profile(agent)
            except Exception as e:
                log_error(e)
                log_error(f'[ERROR] Failed to extract description from {agent}')
                failed_agents.append(agent)

        skipped_agents.extend(failed_agents)
        return described_agents, skipped_agents
