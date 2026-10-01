config_berlin_sumo = {
    'workers': 1,
    'num_agents': 10,
    # 3,576,870 * (1/100) = 35769 -> https://esa.un.org/unpd/wup/  / https://worldpopulationreview.com/cities/germany/berlin
    'storage_path': 'results/berlin-sumo',
    'buildings_file': 'data/taz/berlin_buildings.gpkg',
    'taz_file': 'data/taz/berlin_taz_zones.gpkg',
    'net_file': 'data/open_street_map/berlin/berlin.net.xml',
    'poly_file': 'data/open_street_map/berlin/berlin.poly.xml',
    'v_types_file': 'data/open_street_map/berlin/vtypes.xml',
    'pt_stops_file': 'data/open_street_map/berlin/gtfs_pt_stops.add.xml',
    'pt_vehicles_file': 'data/open_street_map/berlin/validated.gtfs_pt_vehicles.add.xml',
    'census_paths': {
        "persons": "data/census/MiD2017_Personen.csv",
        "household": "data/census/MiD2017_Haushalte.csv",
    },
    'exclude_too_young': True,
    'exclude_too_old': False
}

config_wedding_sumo = {
    'workers': 1,
    'num_agents': 10,
    'storage_path': 'results/wedding-sumo',
    'buildings_file': 'data/taz/wedding_buildings.gpkg',
    'taz_file': 'data/taz/wedding_taz_zones.gpkg',
    'net_file': 'data/open_street_map/wedding/wedding.net.xml',
    'poly_file': 'data/open_street_map/wedding/wedding.poly.xml',
    'v_types_file': 'data/open_street_map/wedding/vtypes.xml',
    'pt_stops_file': 'data/open_street_map/wedding/gtfs_pt_stops.add.xml',
    'pt_vehicles_file': 'data/open_street_map/wedding/gtfs_pt_vehicles.add.xml',
    'census_paths': {
        "persons": "data/census/MiD2017_Personen.csv",
        "household": "data/census/MiD2017_Haushalte.csv",
    },
    'exclude_too_young': True,
    'exclude_too_old': False
}

config_wedding_sumo_UAM = {
    'workers': 1,
    'num_agents': 20,
    'storage_path': 'results/wedding-sumo-UAM',
    'buildings_file': 'data/taz/wedding_buildings.gpkg',
    'taz_file': 'data/taz/wedding_taz_zones.gpkg',

    'net_file': 'data/open_street_map/wedding/wedding.net.xml',
    'poly_file': 'data/open_street_map/wedding/wedding.poly.xml',

    'v_types_file': 'data/open_street_map/wedding/vtypes.xml',
    'pt_stops_file': 'data/open_street_map/wedding/gtfs_pt_stops.add.xml',
    'pt_vehicles_file': 'data/open_street_map/wedding/gtfs_pt_vehicles.add.xml',

    'taxi_fleet_files': {
        'air_taxi': 'data/open_street_map/wedding/generated_air_taxi_fleet.rou.xml',
    },
    'taxi_fleet_sizes': {
        'air_taxi': 3,
    },
    'air_taxi_wait_time_s': 180,
    'air_taxi_pickup_time_s': 60,
    'air_taxi_dropoff_time_s': 60,
    'air_taxi_speed_mps': 50.0,

    'uam_hubs_file': 'data/open_street_map/wedding_uam/wedding_uam_hubs.json',

    'census_paths': {
        "persons": "data/census/MiD2017_Personen.csv",
        "household": "data/census/MiD2017_Haushalte.csv",
    },
    'exclude_too_young': True,
    'exclude_too_old': False
}

