from pathlib import Path

import numpy as np
import pandas as pd

from model.seed import MiD2017Seed

seed_config = {
    'federal_state': 11,
    'base_census': 'persons',
    'census_variables': {
        'persons': {
            'codebook_sheet': 'Persons',
            'source_key': 'H_ID',
            'base_key': 'H_ID',
            'attribute_variables': [  # 'P_VAUTO', 'vpedrad',
                'HP_SEX', 'HP_ALTER',
                'HP_TAET', 'P_PENDLER', 'P_HOFF1', 'P_HOFF2',
                'P_BIL',  # 'gesein', 'mobein',
                'hhgr_gr', 'hheink_gr2', 'oek_status', 'hhtyp2', 'P_GES2',
                'P_NUTZ_AUTO', 'P_EINVM_AUTO', 'P_NUTZ_FUSS', 'P_EINVM_FUSS', 
                'P_NUTZ_OPNV',  'P_EINVM_OPNV','P_NUTZ_RAD', 'P_EINVM_RAD',
            ],
            'additional_variables': [
                'HP_ID', 'H_ID', 'P_ID',
                'P_GEW',
                'ST_MONAT', 'ST_JAHR', 'ST_WOTAG', 'ST_WOCHE',
                'P_STWG1', 'P_RBW',
                'anzwege3', 'anzkm',
                'persmin1', 'P_FKARTE'
            ],
        },
        'household': {
            'codebook_sheet': 'Households',
            'source_key': 'H_ID',
            'base_key': 'H_ID',
            'attribute_variables': [],
            'additional_variables': [
                'H_ANZRAD', 'H_GR', 'H_ID', 'H_ANZAUTO'
            ],
        }
    },
    'variables_mapping_path': 'data/census/TRANSLATEDMiD2017_Codepläne_B1_Standard.xlsx'
}


class SeedGeneratorMiD:
    def __init__(self, census_paths, seed_config=seed_config):
        self.seed_config = seed_config
        self.census_paths = normalize_census_paths(census_paths, seed_config['base_census'])
        self.census_frames = {
            census_name: load_census_frame(census_path, seed_config['federal_state'])
            for census_name, census_path in self.census_paths.items()
        }

        census_variables = seed_config['census_variables']
        for census_name in census_variables:
            if census_name not in self.census_frames:
                raise KeyError(f'No census data was provided for config entry {census_name!r}')

        variables_mapping_path = seed_config['variables_mapping_path']
        code_label_mapping = load_codebook_map(
            variables_mapping_path,
            {config.get('codebook_sheet', census_name) for census_name, config in census_variables.items()}
        )

        base_census_name = seed_config['base_census']
        base_data = self.census_frames[base_census_name]
        lookup_frames = build_lookup_frames(self.census_frames, census_variables, base_census_name)

        self.original_data = base_data.apply(
            lambda row: (
                collect_labels(row, census_variables, code_label_mapping, lookup_frames, 'attribute_variables'),
                collect_labels(row, census_variables, code_label_mapping, lookup_frames, 'additional_variables'),
                row['P_GEW']
            ),
            axis=1
        ).tolist()

    def generate_seeds(self, num_agents: int) -> list[MiD2017Seed]:
        """
        Generate exactly num_agents agents using a Truncate, Replicate, Sample (TRS) approach.
        The method truncates the fractional part of each scaled weight, then samples the remaining
        agents based on the fractional parts (probabilistic sampling).
        """
        seeds = []
        total_weight = sum(p for (_, _, p) in self.original_data)
        scale = num_agents / total_weight if total_weight > 0 else 1

        integer_counts = []
        fractional_parts = []

        for attributes, additional_data, weight in self.original_data:
            scaled_weight = weight * scale
            int_part = int(np.floor(scaled_weight))
            frac_part = scaled_weight - int_part
            integer_counts.append((attributes, additional_data, int_part))
            fractional_parts.append((attributes, additional_data, frac_part))

        agent_id = 0
        for attributes, additional_data, count in integer_counts:
            for _ in range(count):
                seeds.append(MiD2017Seed(agent_id, attributes, additional_data))
                agent_id += 1

        remaining = num_agents - len(seeds)
        if remaining > 0:
            total_fraction = sum(frac for _, _, frac in fractional_parts)
            probabilities = [frac / total_fraction if total_fraction > 0 else 0 for _, _, frac in fractional_parts]
            sampled_indices = np.random.choice(len(fractional_parts), size=remaining, p=probabilities)
            for idx in sampled_indices:
                attributes, additional_data, _ = fractional_parts[idx]
                seeds.append(MiD2017Seed(agent_id, attributes, additional_data))
                agent_id += 1

        return seeds


def normalize_census_paths(census_paths, base_census_name):
    if isinstance(census_paths, str):
        return {base_census_name: census_paths}

    if isinstance(census_paths, (list, tuple)):
        normalized = {}
        for census_path in census_paths:
            census_name = Path(census_path).stem.lower()
            normalized[census_name] = census_path
        return normalized

    if isinstance(census_paths, dict):
        return census_paths

    raise TypeError(f'Unsupported census_paths type: {type(census_paths)}')


def load_census_frame(census_path, federal_state):
    data = pd.read_csv(census_path, sep=';')
    if 'BLAND' in data.columns:
        data = data[data['BLAND'] == federal_state]
    if 'P_GEW' in data.columns:
        data['P_GEW'] = data['P_GEW'].astype(str).str.replace(',', '.').astype(float)
    return data


def build_lookup_frames(census_frames, census_variables, base_census_name):
    lookup_frames = {}
    for census_name, census_config in census_variables.items():
        if census_name == base_census_name:
            continue
        source_key = census_config.get('source_key')
        if not source_key:
            raise KeyError(f'Census config {census_name!r} must define source_key when it is not the base census')
        lookup_frames[census_name] = census_frames[census_name].drop_duplicates(subset=[source_key]).set_index(source_key)
    return lookup_frames


def collect_labels(base_row, census_variables, code_label_mapping, lookup_frames, variable_type):
    collected_labels = {}
    base_census_name = seed_config['base_census']
    base_source_key = census_variables[base_census_name].get('source_key')
    for census_name, census_config in census_variables.items():
        variables = census_config.get(variable_type, [])
        if not variables:
            continue

        sheet_name = census_config.get('codebook_sheet', census_name)
        if census_name in lookup_frames:
            base_key = census_config.get('base_key', base_source_key)
            if base_key not in base_row.index:
                continue
            join_value = base_row[base_key]
            if pd.isna(join_value) or join_value not in lookup_frames[census_name].index:
                continue
            source_row = lookup_frames[census_name].loc[join_value]
        else:
            source_row = base_row

        collected_labels.update(
            map_labels(source_row, variables, code_label_mapping, sheet_name)
        )
    return collected_labels


def create_variable_dict(df):
    columns = get_codebook_columns(df)
    columns_to_ffill = [
        columns['variable'],
        columns['variable_label'],
        columns['measurement_level'],
        columns['format'],
    ]
    df[columns_to_ffill] = df[columns_to_ffill].ffill()

    variable_dict = {}
    grouped = df.groupby(columns['variable'])
    for var, group in grouped:
        value_label_mapping = {
            str(k): str(v)
            for k, v in zip(group[columns['value']], group[columns['value_label']])
        }
        variable_dict[var] = {
            "Variable Label": str(group[columns['variable_label']].iloc[0]),
            "Measurement Level": str(group[columns['measurement_level']].iloc[0]),
            "Format": str(group[columns['format']].iloc[0]),
            "Value Label Mapping": value_label_mapping
        }
    return variable_dict


def get_codebook_columns(df):
    column_options = {
        'variable': ['Variable', 'variable'],
        'variable_label': ['Variable Label', 'Variable label', 'variable label', 'Variablenlabel'],
        'measurement_level': ['Measurement Level', 'Measurement level', 'measurement level', 'Messniveau'],
        'format': ['Format', 'format'],
        'value': ['Value', 'Wert'],
        'value_label': ['Value Label', 'Value label', 'value label', 'Wertelabel'],
    }

    resolved_columns = {}
    for key, options in column_options.items():
        resolved_column = next((column for column in options if column in df.columns), None)
        if resolved_column is None:
            raise KeyError(f'Could not find a matching codebook column for {key}: {options}')
        resolved_columns[key] = resolved_column
    return resolved_columns


def _load_codebook_sheet(xls, file_path, requested_sheet_name):
    actual_sheet_name = resolve_sheet_name(xls.sheet_names, requested_sheet_name)
    return pd.read_excel(file_path, header=1, sheet_name=actual_sheet_name)


def load_codebook_map(file_path, sheet_names):
    excel_dict = {}
    xls = pd.ExcelFile(file_path)
    for requested_sheet_name in sheet_names:
        df = _load_codebook_sheet(xls, file_path, requested_sheet_name)
        excel_dict[requested_sheet_name] = create_variable_dict(df)
    return excel_dict


def resolve_sheet_name(sheet_names, requested_sheet_name):
    aliases = {
        'Persons': ['Persons', 'Personen', 'persons'],
        'Households': ['Households', 'Haushalte', 'households'],
    }
    options = aliases.get(requested_sheet_name, [requested_sheet_name])
    actual_sheet_name = next((sheet_name for sheet_name in options if sheet_name in sheet_names), None)
    if actual_sheet_name is None:
        raise KeyError(f'Could not find a matching sheet for {requested_sheet_name!r}: {options}')
    return actual_sheet_name


def map_labels(person, variables, code_label_mapping, sheet_name):
    sheet_mapping = code_label_mapping[sheet_name]
    long_variable_labels_dict = {}
    for variable in variables:
        if variable not in person.index:
            continue
        code = str(person[variable])
        variable_mapping = sheet_mapping.get(variable)
        if variable_mapping is None:
            long_variable_labels_dict[variable] = code
            continue
        long_variable = variable_mapping['Variable Label']
        label = variable_mapping['Value Label Mapping'].get(code)
        if not label:
            label = code
        long_variable_labels_dict[long_variable] = label
    return long_variable_labels_dict
