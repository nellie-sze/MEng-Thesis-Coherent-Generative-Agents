import traci
import math
import os
import random
import json
import sumolib
from util.logging import log_debug

NET_OFFSET_X = None
NET_OFFSET_Y = None
DEFAULT_TAXI_DISPATCH_PERIOD = "30"
DEFAULT_TAXI_TYPE_ID = "taxi"
DEFAULT_TAXI_LINE_ID = "taxi"
DEFAULT_TAXI_FLEET_SEED = 42


def _set_net_offset(net_file):
    global NET_OFFSET_X, NET_OFFSET_Y
    net = sumolib.net.readNet(net_file)
    NET_OFFSET_X, NET_OFFSET_Y = net.getLocationOffset()


def _load_uam_taxi_edges(uam_hubs_file):
    if not uam_hubs_file:
        raise ValueError("UAM fleet generation requires a configured uam_hubs_file.")

    hub_path = _hub_path(uam_hubs_file)
    with open(hub_path, encoding="utf-8") as handle:
        raw_hubs = json.load(handle)

    taxi_edges = []
    for hub in raw_hubs:
        taxi_edge = hub.get("taxi_edge")
        if taxi_edge and taxi_edge not in taxi_edges:
            taxi_edges.append(taxi_edge)

    if len(taxi_edges) < 2:
        raise ValueError("At least two UAM hub taxi edges are required to generate a UAM fleet.")

    return taxi_edges


def _hub_path(uam_hubs_file):
    return uam_hubs_file if os.path.isabs(uam_hubs_file) else os.path.join(os.getcwd(), uam_hubs_file)


def _parking_edge(net, edge_id, net_file):
    parking_edge_id = edge_id if edge_id.startswith("-") else f"-{edge_id}"
    edge = net.getEdge(parking_edge_id)
    if edge is None:
        raise ValueError(f"UAM hub parking edge {parking_edge_id!r} not found in network {net_file!r}.")
    if not edge.allows("taxi"):
        raise ValueError(f"UAM hub parking edge {parking_edge_id!r} does not allow taxi-class vehicles.")
    return parking_edge_id


def generate_uam_fleet_file(net_file, taxi_fleet_file, fleet_size, uam_hubs_file,
                            taxi_type_id="uamtaxi", taxi_line="taxi:air",
                            seed=DEFAULT_TAXI_FLEET_SEED):
    if not taxi_fleet_file or fleet_size <= 0 or not uam_hubs_file:
        return

    os.makedirs(os.path.dirname(taxi_fleet_file), exist_ok=True)
    net = sumolib.net.readNet(net_file)
    taxi_edges = _load_uam_taxi_edges(uam_hubs_file)
    valid_edges = []
    for edge_id in taxi_edges:
        valid_edges.append(_parking_edge(net, edge_id, net_file))

    rng = random.Random(seed)
    ordered_edges = list(valid_edges)
    rng.shuffle(ordered_edges)
    fleet_lines = ['<routes>\n']
    for taxi_index in range(fleet_size):
        from_edge = ordered_edges[taxi_index % len(ordered_edges)]
        to_edge = ordered_edges[(taxi_index + 1) % len(ordered_edges)]
        if to_edge == from_edge:
            to_edge = ordered_edges[(taxi_index + 2) % len(ordered_edges)]
        fleet_lines.append(
            f'\t<trip id="taxi_fleet_{taxi_index}" type="{taxi_type_id}" depart="0" '
            f'from="{from_edge}" to="{to_edge}" line="{taxi_line or "taxi:air"}" />\n'
        )
    fleet_lines.append('</routes>\n')

    with open(taxi_fleet_file, "w", encoding="utf-8") as taxi_file:
        taxi_file.writelines(fleet_lines)


def generate_uam_fleet_files(net_file, taxi_fleet_specs):
    for fleet_index, fleet_spec in enumerate(taxi_fleet_specs or []):
        generate_uam_fleet_file(
            net_file,
            fleet_spec.get("file"),
            fleet_spec.get("size", 0),
            fleet_spec.get("uam_hubs_file"),
            taxi_type_id=fleet_spec.get("type_id", "uamtaxi"),
            taxi_line=fleet_spec.get("line", "taxi:air"),
            seed=fleet_spec.get("seed", DEFAULT_TAXI_FLEET_SEED + fleet_index),
        )


def start_sim(net_file, poly_file, v_types_file, pt_stops_file, pt_vehicles_file, taxi_fleet_specs=None):
    _set_net_offset(net_file)
    additional_files = [poly_file, v_types_file, pt_stops_file]
    additional_files = ','.join(filter(None, additional_files))
    taxi_route_files = [fleet_spec.get("file") for fleet_spec in (taxi_fleet_specs or []) if fleet_spec.get("file")]
    route_files = ','.join(filter(None, [pt_vehicles_file, *taxi_route_files]))
    traci.start(
        [
            'sumo',
            '--net-file', net_file,
            '--additional-files', additional_files,
            '--route-files', route_files,
            '--ignore-route-errors', "true",
            '--no-warnings', 'true',
            '--time-to-teleport', '20',
            '--device.taxi.dispatch-algorithm', 'greedy',
            '--device.taxi.dispatch-period', DEFAULT_TAXI_DISPATCH_PERIOD,
            '--device.taxi.idle-algorithm', 'stop',
        ])


def get_num_expected_vehicles():
    return traci.simulation.getMinExpectedNumber()


def simulation_step():
    traci.simulationStep()


def stop_sim():
    traci.close()


def get_polygons_with_parameters(parameters):
    polygon_ids = get_polygon_ids()
    polygon_parameters_list = []
    for polygon_id in polygon_ids:
        polygon_parameters = {'polygon_id': polygon_id, 'parameters': []}
        has_at_least_one_parameter = False
        for parameter in parameters:
            key, value = traci.polygon.getParameterWithKey(polygon_id, parameter)
            if value != '':
                has_at_least_one_parameter = True
            polygon_parameters['parameters'].append({key: value})
        if has_at_least_one_parameter:
            polygon_parameters_list.append(polygon_parameters)
    return polygon_parameters_list


def get_polygons_with_parameter(parameter):
    polygon_ids = get_polygon_ids()
    polygons_with_parameter = []
    for polygon_id in polygon_ids:
        key, value = traci.polygon.getParameterWithKey(polygon_id, parameter)
        if value != '':
            polygon_with_parameter = {'polygon_id': polygon_id, key: value}
            polygons_with_parameter.append(polygon_with_parameter)
    return polygons_with_parameter


def get_polygon_ids():
    return traci.polygon.getIDList()


def get_polygon_position(polygon_id):
    return get_polygon_shape(polygon_id)[0]


def get_geo_coordinates(cart_coordinates):
    lon_lat = traci.simulation.convertGeo(cart_coordinates[0], cart_coordinates[1])
    # We want lat and then lon and not the other way round
    return [lon_lat[1], lon_lat[0]]


def get_cart_coordinates(lon, lat):
    lon_lat = traci.simulation.convertGeo(lon, lat, fromGeo=True)
    return [lon_lat[0], lon_lat[1]]


def get_polygon_shape(polygon_id):
    return traci.polygon.getShape(polygon_id)


def find_route(start_position, end_position, v_class='passenger', v_type='DEFAULT_VEHTYPE'):
    from_edge = get_road_edge(start_position, v_class)
    to_edge = get_road_edge(end_position, v_class)
    return traci.simulation.findRoute(from_edge, to_edge, vType=v_type)


def find_route_from_edges(from_edge, to_edge, v_type='DEFAULT_VEHTYPE'):
    return traci.simulation.findRoute(from_edge, to_edge, vType=v_type)


def get_road_edge(start_position, v_class):
    if NET_OFFSET_X is None or NET_OFFSET_Y is None:
        raise RuntimeError("Network offset has not been initialized. Call start_sim() before get_road_edge().")
    #log_debug(f"initial coords: {start_position[0]}, {start_position[1]}")
    x = start_position[0] + NET_OFFSET_X
    y = start_position[1] + NET_OFFSET_Y
    #log_debug(f"attempting convertroad with the following: {x}, {y}")
    from_edge, a, b = traci.simulation.convertRoad(x, y, vClass=v_class)
    conv_x, conv_y = traci.simulation.convert2D(from_edge, a, b)
    #log_debug(f"ID: {from_edge} has distance {math.sqrt((conv_x - x) ** 2 + (conv_y - y) ** 2)} metres from start point")
    return from_edge


def find_intermodal_route(start_position, end_position, arrival_time, modes='public'):
    from_edge = get_road_edge(start_position, 'pedestrian')
    to_edge = get_road_edge(end_position, 'pedestrian')
    route_estimation = find_intermodal_route_from_edges(from_edge, to_edge, arrival_time, modes=modes)
    estimated_travel_time = sum(stage.travelTime for stage in route_estimation)
    departure_time = arrival_time - estimated_travel_time
    return find_intermodal_route_from_edges(from_edge, to_edge, departure_time, modes=modes)


def find_intermodal_route_from_edges(from_edge, to_edge, departure_time, modes='public'):
    return traci.simulation.findIntermodalRoute(from_edge, to_edge, modes, depart=departure_time)


def add_vehicle_route(route, type_id):
    # Vehicle execution: route['route'] is a full edge list and we spawn a SUMO vehicle.
    route_id = route['route_id']
    edges = route['route']
    departure_time = route['departure_time']
    if edges:
        add_route(route_id, edges)
        add_vehicle(route_id, route_id, departure_time, type_id)
        return route
    raise Exception('No vehicle route found')


def add_vehicle(veh_id, route_id, departure_time, vehicle_type):
    traci.vehicle.add(str(veh_id), str(route_id), depart=departure_time, typeID=vehicle_type)


def get_vehicle_class(type_id):
    return traci.vehicletype.getVehicleClass(type_id)


def add_route(route_id, edges, v_class='passenger'):
    traci.route.add(str(route_id), edges)


def add_pedestrian(route, type_id="DEFAULT_PEDTYPE"):
    # Pedestrian execution: create a person and attach a walking stage over the edge list.
    route_id = route['route_id']
    edges = route['route']
    departure_time = route['departure_time']
    from_edge = edges[0]

    traci.person.add(route_id, from_edge, 0, departure_time, typeID=type_id)
    traci.person.appendWalkingStage(route_id, edges, arrivalPos=1.0)
    return route


def add_intermodal(route, type_id="DEFAULT_PEDTYPE"):
    # Intermodal execution: route['route'] only stores origin/destination edges; SUMO expands this into staged PT/walk travel.
    route_id = route['route_id']
    stages = route['route']
    from_edge = stages[0]
    to_edge = stages[1]
    departure_time = route['departure_time']

    stages = find_intermodal_route_from_edges(from_edge, to_edge, departure_time, modes='public')

    traci.person.add(route_id, from_edge, 0, departure_time, typeID=type_id)
    for stage in stages:
        traci.person.appendStage(route_id, stage)
    return route


def add_stage_plan(route, type_id="DEFAULT_PEDTYPE"):
    route_id = route['route_id']
    details = route.get('details', {})
    stage_plan = details.get('stage_plan') or route.get('stage_plan') or []
    departure_time = route['departure_time']
    if not stage_plan:
        raise ValueError("Taxi stage plan is missing.")

    first_stage = stage_plan[0]
    if first_stage['kind'] == 'walk':
        from_edge = first_stage['edges'][0]
    else:
        from_edge = first_stage['from_edge']

    traci.person.add(route_id, from_edge, 0, departure_time, typeID=type_id)

    for stage in stage_plan:
        if stage['kind'] == 'walk':
            traci.person.appendWalkingStage(route_id, stage['edges'], arrivalPos=1.0)
        elif stage['kind'] == 'ride':
            traci.person.appendDrivingStage(route_id, stage['to_edge'], stage.get('lines', DEFAULT_TAXI_LINE_ID))
        else:
            raise ValueError(f"Unsupported stage kind: {stage['kind']}")
    return route


def edge_near(position, v_class):
    from_edge, _, _ = traci.simulation.convertRoad(position[0], position[1], vClass=v_class)
    return from_edge
