import argparse
import copy
import json
import math
import os
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / 'src'

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

os.environ.setdefault('SUMO_HOME', str(PROJECT_ROOT / 'sumo-1.26.0'))
SUMO_BIN = Path(os.environ['SUMO_HOME']) / 'bin'
os.environ['PATH'] = f"{SUMO_BIN}{os.pathsep}{os.environ.get('PATH', '')}"
if hasattr(os, 'add_dll_directory') and SUMO_BIN.exists():
    os.add_dll_directory(str(SUMO_BIN))

try:
    import libsumo as traci
except ImportError:
    import traci

from config.vehicle_config import vehicle_configs
from model.agent import Agent
from model.vehicle import ExecutionKind, Vehicle
from util.file import write_file
from util.trips import generate_trips_xml, merge_trips_xml

UAM_HUBS = []


def start_sim(net_file, v_types_file, pt_stops_file, pt_vehicles_file):
    additional_files = [file_path for file_path in (v_types_file, pt_stops_file) if file_path]
    traci.start(
        [
            'sumo',
            '--net-file', net_file,
            '--additional-files', ','.join(additional_files),
            '--route-files', pt_vehicles_file,
            '--no-warnings', 'true',
        ])


def get_v_class(means_of_transport):
    normalized_means_of_transport = str(means_of_transport).strip().lower()
    matched_config = None
    for vehicle_config in vehicle_configs:
        vehicle_name = str(vehicle_config.get('name', '')).strip().lower()
        if normalized_means_of_transport == vehicle_name:
            matched_config = vehicle_config
            break

    if matched_config is None:
        raise ValueError(f"Unknown vehicle name {means_of_transport!r}")

    vehicle = Vehicle.from_dict(matched_config)
    if vehicle.execution_kind in (ExecutionKind.PEDESTRIAN, ExecutionKind.INTERMODAL):
        return 'pedestrian'
    return traci.vehicletype.getVehicleClass(vehicle.sumo_type_id)


def get_new_random_edge(edge_id, meter_interval, means_of_transport):
    x, y = traci.simulation.convert2D(edge_id, 0, laneIndex=0, toGeo=False)
    new_x = x + random.uniform(*meter_interval)
    new_y = y + random.uniform(*meter_interval)
    v_class = get_v_class(means_of_transport)
    new_edge_id, _, _ = traci.simulation.convertRoad(new_x, new_y, vClass=v_class)
    return new_edge_id


def _load_uam_hubs(uam_hubs_file):
    if not uam_hubs_file:
        return []
    hub_path = uam_hubs_file if os.path.isabs(uam_hubs_file) else os.path.join(os.getcwd(), uam_hubs_file)
    with open(hub_path, encoding='utf-8') as handle:
        return json.load(handle)


def _normalise_uam_hubs(raw_hubs):
    normalised = []
    for index, hub in enumerate(raw_hubs or []):
        if not isinstance(hub, dict):
            continue
        hub_id = hub.get('id', f'hub_{index}')
        x = hub.get('x')
        y = hub.get('y')
        access_edge = hub.get('access_edge')
        taxi_edge = hub.get('taxi_edge')
        if x is None or y is None or access_edge is None or taxi_edge is None:
            continue
        normalised.append({
            'id': hub_id,
            'xy': (x, y),
            'access_edge': access_edge,
            'taxi_edge': taxi_edge,
        })
    return normalised


def _get_air_taxi_route(vehicle, from_edge, to_edge):
    if len(UAM_HUBS) < 2:
        return None

    from_x, from_y = traci.simulation.convert2D(from_edge, 0.0, laneIndex=0, toGeo=False)
    to_x, to_y = traci.simulation.convert2D(to_edge, 0.0, laneIndex=0, toGeo=False)

    start_hub = min(
        UAM_HUBS,
        key=lambda hub: math.hypot(from_x - hub["xy"][0], from_y - hub["xy"][1]),
    )
    end_hub = min(
        UAM_HUBS,
        key=lambda hub: math.hypot(to_x - hub["xy"][0], to_y - hub["xy"][1]),
    )

    if start_hub["id"] == end_hub["id"]:
        return None

    access_walk = None
    if from_edge != start_hub["access_edge"]:
        access_walk = traci.simulation.findIntermodalRoute(
            from_edge,
            start_hub["access_edge"],
            modes="",
            depart=0,
        )
        if not access_walk:
            return None

    hub_boarding_walk = None
    if start_hub["access_edge"] != start_hub["taxi_edge"]:
        hub_boarding_walk = traci.simulation.findIntermodalRoute(
            start_hub["access_edge"],
            start_hub["taxi_edge"],
            modes="",
            depart=0,
        )
        if not hub_boarding_walk:
            return None

    hub_alighting_walk = None
    if end_hub["taxi_edge"] != end_hub["access_edge"]:
        hub_alighting_walk = traci.simulation.findIntermodalRoute(
            end_hub["taxi_edge"],
            end_hub["access_edge"],
            modes="",
            depart=0,
        )
        if not hub_alighting_walk:
            return None

    egress_walk = None
    if end_hub["access_edge"] != to_edge:
        egress_walk = traci.simulation.findIntermodalRoute(
            end_hub["access_edge"],
            to_edge,
            modes="",
            depart=0,
        )
        if not egress_walk:
            return None

    return {
        "access_walk": access_walk,
        "hub_boarding_walk": hub_boarding_walk,
        "air_ride": (start_hub["taxi_edge"], end_hub["taxi_edge"]),
        "hub_alighting_walk": hub_alighting_walk,
        "egress_walk": egress_walk,
    }


def has_route(from_edge_id, to_edge_id, means_of_transport, departure_time):
    matched_config = None
    for vehicle_config in vehicle_configs:
        vehicle_name = str(vehicle_config.get('name', '')).strip().lower()
        if str(means_of_transport).strip().lower() == vehicle_name:
            matched_config = vehicle_config
            break

    if matched_config is None:
        return False

    vehicle = Vehicle.from_dict(matched_config)
    route = None

    if vehicle.execution_kind == ExecutionKind.PEDESTRIAN:
        route = traci.simulation.findIntermodalRoute(from_edge_id, to_edge_id, modes='', depart=departure_time)
        if not route:
            return False
        if not all(getattr(stage, 'type', None) == 2 for stage in route):
            return False

    elif vehicle.execution_kind == ExecutionKind.VEHICLE:
        route = traci.simulation.findRoute(from_edge_id, to_edge_id, vType=vehicle.sumo_type_id)

    elif vehicle.execution_kind == ExecutionKind.INTERMODAL:
        route = traci.simulation.findIntermodalRoute(from_edge_id, to_edge_id, modes='public', depart=departure_time)
        if not route:
            return False
        pt = False
        for stage in route:
            if getattr(stage, 'type', None) != 2:
                pt = True
            elif getattr(stage, 'line', None):
                pt = True
            elif getattr(stage, 'destStop', None):
                pt = True
        if not pt:
            return False

    elif vehicle.execution_kind == ExecutionKind.TAXI:
        if vehicle.taxi_line == 'taxi:air':
            route = _get_air_taxi_route(vehicle, from_edge_id, to_edge_id)
        else:
            route = traci.simulation.findRoute(from_edge_id, to_edge_id, vType=vehicle.sumo_type_id)

    return bool(route)


class AgentsReplicator:
    def __init__(self, net_file, v_types_file, pt_stops_file, pt_vehicles_file, uam_hubs_file, agents_file, output_file):
        self.net_file = net_file
        self.v_types_file = v_types_file
        self.pt_stops_file = pt_stops_file
        self.pt_vehicles_file = pt_vehicles_file
        self.uam_hubs_file = uam_hubs_file

        self.agents_file = agents_file
        self.output_file = output_file

        self.json_agents = None
        self.agents = None

        global UAM_HUBS
        UAM_HUBS = _normalise_uam_hubs(_load_uam_hubs(self.uam_hubs_file))

    def load_file(self):
        with open(self.agents_file, 'r') as file:
            self.json_agents = json.load(file)

    def save_file(self, trip_kind='all'):
        route_descriptions = [
            {'agent_id': agent.id, **route_description}
            for agent in self.agents
            for route_description in (agent.route_descriptions or [])
        ]
        trips_xml, uam_trips_xml = generate_trips_xml(route_descriptions)
        if trip_kind == 'ground':
            output_xml = trips_xml
        elif trip_kind == 'uam':
            output_xml = uam_trips_xml
        else:
            output_xml = merge_trips_xml(trips_xml, uam_trips_xml)
        write_file(self.output_file, output_xml)

    def start_sim(self):
        start_sim(self.net_file, self.v_types_file, self.pt_stops_file, self.pt_vehicles_file)

    def replicate_agents(self, count, time_interval, meter_interval, retries=5):
        self.agents = []
        routes_changed_count = 0
        routes_failed_changing_count = 0
        for json_agent in self.json_agents:
            for index in range(count):
                agent = copy.deepcopy(Agent.from_json(json_agent))
                original_agent_id = int(agent.id)
                if index == 0:
                    agent.id = original_agent_id
                else:
                    agent.id = 50000000 + (original_agent_id * 1000) + index
                if index != 0:
                    for route_index in range(len(agent.route_descriptions)):
                        original_route_id = agent.route_descriptions[route_index]['route_id']
                        replicated_route_id = f"{original_route_id}{index:03d}"
                        agent.route_descriptions[route_index]['route_id'] = replicated_route_id
                        are_valid_edges = False
                        for try_count in range(retries):
                            means_of_transport = agent.route_descriptions[route_index]['means_of_transport']
                            departure_time = max(0.0,
                                                 (float(agent.route_descriptions[route_index]['departure_time']) +
                                                  random.uniform(*time_interval)))
                            agent.route_descriptions[route_index]['departure_time'] = departure_time

                            from_edge_id = agent.route_descriptions[route_index]['route'][0]
                            new_from_edge_id = get_new_random_edge(from_edge_id, meter_interval, means_of_transport)

                            to_edge_id = agent.route_descriptions[route_index]['route'][
                                -1]
                            new_to_edge_id = get_new_random_edge(to_edge_id, meter_interval, means_of_transport)

                            are_valid_edges = has_route(new_from_edge_id, new_to_edge_id, means_of_transport,
                                                        departure_time)
                            if are_valid_edges:
                                agent.route_descriptions[route_index]['route'] = [new_from_edge_id, new_to_edge_id]
                                break
                        if are_valid_edges:
                            routes_changed_count += 1
                        else:
                            routes_failed_changing_count += 1
                self.agents.append(agent)
        return routes_changed_count, routes_failed_changing_count

    def process(self, count, time_interval=(-120, 120), meter_interval=(-1000, 1000), retries=5, trip_kind='all'):
        self.load_file()
        self.start_sim()
        try:
            routes_changed_count, routes_failed_changing_count = self.replicate_agents(count, time_interval, meter_interval,
                                                                                       retries)
            print(f'{routes_changed_count} routes were successfully changed, '
                  f'{routes_failed_changing_count} routes failed to find valid edges ({retries} times)')
            self.save_file(trip_kind=trip_kind)
            print(f'Finished creating {len(self.agents)} from {len(self.json_agents)} agents')
            print(f"Expanded XML content has been saved to {self.output_file}.")
        finally:
            traci.close()


def main():
    parser = argparse.ArgumentParser(
        description="Run the AgentsReplicator with customizable file paths and parameters."
    )
    parser.add_argument(
        '--net-file',
        default='../../data/open_street_map/berlin/berlin.net.xml',
        help='Path to the SUMO network file'
    )
    parser.add_argument(
        '--v-types-file',
        default='',
        help='Path to the SUMO vehicle types XML file'
    )
    parser.add_argument(
        '--pt-stops-file',
        default='../../data/open_street_map/berlin/gtfs_pt_stops.add.xml',
        help='Path to the public transport stops XML file'
    )
    parser.add_argument(
        '--pt-vehicles-file',
        default='../../data/open_street_map/berlin/validated.gtfs_pt_vehicles.add.xml',
        help='Path to the public transport vehicles XML file'
    )
    parser.add_argument(
        '--uam-hubs-file',
        default='',
        help='Path to the UAM hubs JSON file'
    )
    parser.add_argument(
        '--input-file',
        default='../../results/test/agents_4_route_descriptions.json',
        help='Path to the JSON file with agent route descriptions'
    )
    parser.add_argument(
        '--output-file',
        default='test.xml',
        help='Path where the expanded XML will be saved'
    )
    parser.add_argument(
        '--count',
        type=int,
        default=1,
        help='Number of times to replicate agents'
    )
    parser.add_argument(
        '--time-interval',
        nargs=2,
        type=int,
        metavar=('START', 'END'),
        default=[-300, 300],
        help='Time interval for agent injection (start, end)'
    )
    parser.add_argument(
        '--meter-interval',
        nargs=2,
        type=int,
        metavar=('START', 'END'),
        default=[-500, 500],
        help='Distance interval for agent injection (start, end)'
    )
    parser.add_argument(
        '--trip-kind',
        choices=['all', 'ground', 'uam'],
        default='all',
        help='Which trip subset to write to the output XML'
    )

    args = parser.parse_args()

    replicator = AgentsReplicator(
        args.net_file,
        args.v_types_file,
        args.pt_stops_file,
        args.pt_vehicles_file,
        args.uam_hubs_file,
        args.input_file,
        args.output_file
    )
    replicator.process(
        count=args.count,
        time_interval=(args.time_interval[0], args.time_interval[1]),
        meter_interval=(args.meter_interval[0], args.meter_interval[1]),
        trip_kind=args.trip_kind,
    )


if __name__ == '__main__':
    main()
