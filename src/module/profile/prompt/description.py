from model.seed import Seed


def build_profile_prompt(seed: Seed):
    home_types = ['student_accomodation', 'house', 'apartments', 'dormitory', 'detached', 'residential']
    home_types_str = ', '.join(home_types)
    return (f'Sample attributes from the Berlin population:\n{seed.get_attributes_string()}\n'
            f'Imagine a realistic person with these attributes. Do not make any assumptions about vehicle ownership. '
            f'Write a specific one paragraph description:\n'
            f'Also select the most appropriate home type from the following based on the person\'s characteristics:\n'
            f'{home_types_str}\n'
            f'Do not include any explanations, only provide a  RFC8259 compliant JSON response  following this format '
            f'without deviation.\n'
            f'{{"persona_description":"realistic one paragraph description of someone with these attributes","home_type":"one of the home types that best fits the person"}}\n'
            f"Your response must be in English.\n"
            f'The JSON Response:\n')
