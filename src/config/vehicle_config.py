""" 'name': how the vehicle is referred to in code,
'prompt_reference': how the vehicle is referred to in LLM prompts,
'SUMO_typeID': SUMO type ID to be used for this vehicle in the SUMO simulation. Used to infer vClass
'execution_kind': currently either 'vehicle', 'pedestrian', 'public_transport', or 'taxi' (used for execution in the simulation) """

"""     
SETTINGS FOR VEHICLE CONFIGURATION
Default values are indicated by -dashes-. If there is no default value, the field is required.
{
    'name': must be unique 
    'prompt_reference': how the vehicle is referred to in LLM prompts, can be identical across multiple vehicles,
    'SUMO_typeID': 'DEFAULT_VEHTYPE' | 'DEFAULT_BIKETYPE' | 'DEFAULT_PEDTYPE',
    'execution_kind': 'vehicle' | 'pedestrian' | 'public_transport' | 'taxi',
    'cost': -Free- | name a function defined in cost_functions.py
    'ownable': True, -False-
    'taxi_line': optional SUMO taxi fleet selector such as taxi:road or taxi:air
    'taxi_fleet_file': optional default generated fleet file for this service
    'ownership_probability': {
        'type': fixed | ratio,
        'numerator': 
        'denominator': 
    },
    'rules': {
        'ownership': -none- | required | optional,
        'first_use_location': -anywhere- | from_home
        'lifecycle': {
            'activation': -none- | on_start | automatic
            'deactivation': -none- | at_home | at_stop | end_of_day | never
            'linked_attribute':
            'sticky': {
                'enabled': True | -False-
                'exceptions': [
                    {
                        'allowed_vehicles': 
                        'duration_threshold': int
                        'distance_threshold': int
                    },
                ],
            },
        },
    },
}
 """
vehicle_configs = [
    # CAR
    {
        'name': 'personal_car',
        'prompt_reference': 'personal car',
        'SUMO_typeID': 'DEFAULT_VEHTYPE',
        'execution_kind': 'vehicle',
        'cost': 'owned_car_cost',
        'ownable': True,
        'ownership_probability': {
            'type': 'ratio',
            'numerator': 'Number of cars in HH',
            'denominator': 'household size'
        },
        'rules': {
            'ownership': 'required',
            'first_use_location': 'from_home',
            'lifecycle': {
                'activation': 'on_start',
                'deactivation': 'at_home',
                'sticky': {
                    'enabled': True,
                    'exceptions': [
                        {
                            'allowed_vehicles': ['pedestrian', 'PT_single', 'PT_day_pass', 'PT_monthly_pass', '9_euro_PT_monthly_pass'],
                            'duration_threshold': 15,
                            'distance_threshold': None,
                        },
                    ],
                },
            },
        },
    },

    # ROAD TAXI
    {
        'name': 'road_taxi',
        'prompt_reference': 'road taxi',
        'SUMO_typeID': 'DEFAULT_VEHTYPE',
        'execution_kind': 'vehicle',
        'cost': 'taxi_cost',
        'ownable': False,
    },

    # AIR TAXI
    {
        'name': 'air_taxi',
        'prompt_reference': 'air taxi',
        'SUMO_typeID': 'uamtaxi',
        'execution_kind': 'taxi',
        'cost': 'air_taxi_cost',
        'taxi_line': 'taxi:air',
    },

    # RENTAL CAR
    {
        'name': 'rental_car',
        'prompt_reference': 'rental car',
        'SUMO_typeID': 'DEFAULT_VEHTYPE',
        'execution_kind': 'vehicle',
        'cost': 'daily_rental_car_cost',
        'ownable': False,
        'rules': {
            'ownership': 'none',
            'first_use_location': 'anywhere',
            'lifecycle': {
                'activation': 'on_start',
                'deactivation': 'at_stop',
                'sticky': {
                    'enabled': False,
                    'exceptions': [],
                },
            },
        },
    },

    # BIKE
    {
        'name': 'personal_bicycle',
        'prompt_reference': 'personal bicycle',
        'SUMO_typeID': 'DEFAULT_BIKETYPE',
        'execution_kind': 'vehicle',
        'cost': 'free',
        'ownable': True,
        'ownership_probability': {
            'type': 'ratio',
            'numerator': 'Number of bicycles in the household',
            'denominator': 'household size'
        },
        'rules': {
            'ownership': 'required',
            'first_use_location': 'from_home',
            'lifecycle': {
                'activation': 'on_start',
                'deactivation': 'at_home',
                'sticky': {
                    'enabled': True,
                    'exceptions': [
                        {
                            'allowed_vehicles': ['pedestrian', 'PT_single', 'PT_day_pass', 'PT_monthly_pass', '9_euro_PT_monthly_pass'],
                            'duration_threshold': None,
                            'distance_threshold': None,
                        },
                    ],
                },
            },
        },
    },

    # RENTAL BIKE
    {
        'name': 'rental_bicycle',
        'prompt_reference': 'rental bicycle',
        'SUMO_typeID': 'DEFAULT_BIKETYPE',
        'execution_kind': 'vehicle',
        'cost': 'long_term_rental_bike_cost',
        'rules': {
            'ownership': 'none',
            'first_use_location': 'anywhere',
            'lifecycle': {
                'activation': 'on_start',
                'deactivation': 'at_stop',
                'sticky': {
                    'enabled': False,
                    'exceptions': [],
                },
            },
        },
    },

    # ONE-USE BIKE
    {
        'name': 'one_use_bike',
        'prompt_reference': 'single-use bicycle',
        'SUMO_typeID': 'DEFAULT_BIKETYPE',
        'execution_kind': 'vehicle',
        'cost': 'short_term_rental_bike_cost',
        'rules': {
            'ownership': 'none',
        },
    },

    # PEDESTRIAN
    {
        'name': 'pedestrian',
        'prompt_reference': 'walk',
        'SUMO_typeID': 'DEFAULT_PEDTYPE',
        'execution_kind': 'pedestrian',
        'rules': {
            'ownership': 'none',
        },
    },

    # PUBLIC TRANSPORT SINGLE TICKET
    {
        'name': 'PT_single',
        'prompt_reference': 'public transport single ticket',
        'SUMO_typeID': 'DEFAULT_PEDTYPE',
        'execution_kind': 'public_transport',
        'cost': 'public_transport_cost',
        'rules': {
            'ownership': 'none',
        },
    },

    # PUBLIC TRANSPORT DAY PASS
    {
        'name': 'PT_day_pass',
        'prompt_reference': 'public transport day pass',
        'SUMO_typeID': 'DEFAULT_PEDTYPE',
        'execution_kind': 'public_transport',
        'cost': 'public_transport_daily_cost',
        'rules': {
            'ownership': 'none',
            'first_use_location': 'anywhere',
            'lifecycle': {
                'activation': 'on_start',
                'deactivation': 'end_of_day',
                'sticky': {
                    'enabled': False,
                    'exceptions': [],
                },
                'conflict': ['PT_single'],
            },
        },
    },

    # FULL COST PUBLIC TRANSPORT MONTHLY PASS
    {
        'name': 'PT_monthly_pass',
        'prompt_reference': 'public transport monthly pass',
        'SUMO_typeID': 'DEFAULT_PEDTYPE',
        'execution_kind': 'public_transport',
        'cost': 'public_transport_monthly_cost',
        'rules': {
            'ownership': 'none',
            'first_use_location': 'anywhere',
            'lifecycle': {
                'activation': 'automatic',
                'deactivation': 'never',
                'linked_attribute': 'travel_pass',
                'linked_value': True,
                'sticky': {
                    'enabled': False,
                    'exceptions': [],
                },
                'conflict': ['PT_single', 'PT_day_pass'],
            },
        },
    },

    # 9 EURO PUBLIC TRANSPORT MONTHLY PASS
    {
        'name': '9_euro_PT_monthly_pass',
        'prompt_reference': 'public transport monthly pass',
        'SUMO_typeID': 'DEFAULT_PEDTYPE',
        'execution_kind': 'public_transport',
        'cost': 'discount_public_transport_monthly_cost',
        'rules': {
            'ownership': 'none',
            'first_use_location': 'anywhere',
            'lifecycle': {
                'activation': 'automatic',
                'deactivation': 'never',
                'linked_attribute': 'travel_pass',
                'linked_value': True,
                'sticky': {
                    'enabled': False,
                    'exceptions': [],
                },
                'conflict': ['PT_single', 'PT_day_pass'],
            },
        },
    },
]
