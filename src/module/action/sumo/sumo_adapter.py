import json
import math
import os

from module.action.sumo.traci_wrapper import start_sim, get_num_expected_vehicles, \
    simulation_step, stop_sim, find_route, add_pedestrian, \
    add_vehicle_route, find_intermodal_route, add_intermodal, add_stage_plan, get_vehicle_class, get_road_edge, \
    find_route_from_edges, find_intermodal_route_from_edges, generate_uam_fleet_files
from model.building import Building
from model.possible_route import PossibleRoute
from model.vehicle import VehicleRegistry, ExecutionKind
from util.logging import log_error, log_debug, log_warning


class SumoAdapter:
    def __init__(self, urban_sampler, net_file, poly_file, v_types_file, pt_stops_file, pt_vehicles_file, sim_config, vehicle_registry: VehicleRegistry):
        self.urban_sampler = urban_sampler
        self.building_categories = urban_sampler.get_attribute_values()
        self.vehicle_registry = vehicle_registry
        self.sim_config = sim_config or {}
        self.net_file = net_file
        self.air_taxi_wait_time_s = self.sim_config.get("air_taxi_wait_time_s", 180)
        self.air_taxi_pickup_time_s = self.sim_config.get("air_taxi_pickup_time_s", 60)
        self.air_taxi_dropoff_time_s = self.sim_config.get("air_taxi_dropoff_time_s", 60)
        self.air_taxi_speed_mps = self.sim_config.get("air_taxi_speed_mps", 50.0)
        self.uam_hubs = []

        self.start_sim(net_file, poly_file, v_types_file, pt_stops_file, pt_vehicles_file)
        self.uam_hubs = self._normalise_uam_hubs(self._load_uam_hubs())

    def start_sim(self, net_file, poly_file, v_types_file, pt_stops_file, pt_vehicles_file):
        start_sim(
            net_file,
            poly_file,
            v_types_file,
            pt_stops_file,
            pt_vehicles_file,
        )

    def prepare_taxi_fleet_files(self, fleet_file_overrides=None, fleet_size_overrides=None):
        temporary_config = dict(self.sim_config)
        if fleet_file_overrides is not None:
            temporary_config["taxi_fleet_files"] = fleet_file_overrides
        if fleet_size_overrides is not None:
            temporary_config["taxi_fleet_sizes"] = fleet_size_overrides

        fleet_specs = self._build_taxi_fleet_specs(temporary_config)
        generate_uam_fleet_files(self.net_file, fleet_specs)
        return fleet_specs

    def should_continue_sim(self):
        num_vehicles = get_num_expected_vehicles()
        return True if num_vehicles > 0 else False

    def simulation_step(self):
        simulation_step()

    def stop_sim(self):
        stop_sim()

    def row_to_building(self, row, parameters):
        polygon_id = row['id']

        location = row['geometry'].centroid

        building = Building(polygon_id=polygon_id, parameters=parameters, location=location)
        return building

    def get_random_apartment(self, home_type):
        try:
            apartment = self.urban_sampler.sample_home(home_type)
        except ValueError:
            log_error(f"Failed to find home of type:{home_type}. Falling back to apartment.")
            apartment = self.urban_sampler.sample_home()
        apartment = self.row_to_building(apartment.iloc[0], [home_type])
        return apartment

    def get_building_with(self, reference_point, attribute_value):
        building = self.urban_sampler.sample_nearby(reference_point, attribute_value=attribute_value)
        building = self.row_to_building(building.iloc[0], [attribute_value])
        return building

    def get_building_by_id(self, polygon_id, attribute_value=None):
        matches = self.urban_sampler.buildings[self.urban_sampler.buildings["id"].astype(str) == str(polygon_id)]
        if matches.empty:
            raise ValueError(f"No building found for polygon_id={polygon_id!r}")

        parameters = [attribute_value] if attribute_value is not None else []
        return self.row_to_building(matches.iloc[0], parameters)

    def get_v_class(self, vehicle):
        return get_vehicle_class(vehicle.sumo_type_id)

    def _build_taxi_fleet_specs(self, sim_config=None):
        sim_config = sim_config or self.sim_config
        fleet_file_overrides = sim_config.get("taxi_fleet_files") or {}
        fleet_size_overrides = sim_config.get("taxi_fleet_sizes") or {}
        fleet_specs = []
        for vehicle in self.vehicle_registry.vehicles:
            if vehicle.execution_kind != ExecutionKind.TAXI:
                continue
            if vehicle.taxi_line != "taxi:air":
                continue
            fleet_file = fleet_file_overrides.get(vehicle.name, vehicle.taxi_fleet_file)
            fleet_size = fleet_size_overrides.get(vehicle.name, 0)
            if fleet_size <= 0:
                continue
            if not fleet_file:
                continue
            fleet_specs.append({
                "file": fleet_file,
                "size": fleet_size,
                "type_id": vehicle.sumo_type_id,
                "line": vehicle.taxi_line,
                "uam_hubs_file": sim_config.get("uam_hubs_file"),
            })
        return fleet_specs

    def _load_uam_hubs(self):
        hub_file = self.sim_config.get("uam_hubs_file")
        if not hub_file:
            return []
        hub_path = hub_file if os.path.isabs(hub_file) else os.path.join(os.getcwd(), hub_file)
        with open(hub_path, encoding="utf-8") as handle:
            return json.load(handle)

    def _normalise_uam_hubs(self, raw_hubs):
        normalised = []
        for index, hub in enumerate(raw_hubs or []):
            if isinstance(hub, dict):
                hub_id = hub.get("id", f"hub_{index}")
                x = hub.get("x")
                y = hub.get("y")
                access_edge = hub.get("access_edge")
                taxi_edge = hub.get("taxi_edge")
            else:
                hub_id = f"hub_{index}"
                x, y = hub
                access_edge = None
                taxi_edge = None
            if x is None or y is None:
                continue
            if access_edge is None:
                log_warning(
                    f"Skipping UAM hub {hub_id}: no access_edge provided. "
                    "Rebuild the UAM scenario so hub metadata includes pedestrian access edges."
                )
                continue
            if taxi_edge is None:
                log_warning(
                    f"Skipping UAM hub {hub_id}: no taxi_edge provided. "
                    "Rebuild the UAM scenario so hub metadata includes taxi boarding edges."
                )
                continue
            normalised.append({
                "id": hub_id,
                "xy": (x, y),
                "access_edge": access_edge,
                "taxi_edge": taxi_edge,
            })
        return normalised

    @staticmethod
    def _euclidean_distance(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    @staticmethod
    def _is_valid_walk_route(route, from_edge, to_edge):
        return (
            bool(route)
            and bool(route[0].edges)
            and route[0].edges[0] == from_edge
            and route[-1].edges[-1] == to_edge
        )

    @staticmethod
    def _is_valid_intermodal_route(route, from_edge, to_edge):
        return (
            bool(route)
            and bool(getattr(route[0], "edges", None))
            and route[0].edges[0] == from_edge
            and route[-1].edges[-1] == to_edge
        )

    @staticmethod
    def _intermodal_uses_public_transport(route):
        if not route:
            return False
        for stage in route:
            if getattr(stage, "type", None) != 2:
                return True
            if getattr(stage, "line", None):
                return True
            if getattr(stage, "destStop", None):
                return True
        return False

    def _summarise_access_stage(self, route, from_edge, to_edge):
        if not route:
            return []
        if self._intermodal_uses_public_transport(route):
            return [{
                "kind": "personTrip",
                "from_edge": from_edge,
                "to_edge": to_edge,
                "modes": "public",
            }]
        return [{
            "kind": "walk",
            "edges": list(stage.edges),
        } for stage in route]

    def _best_access_hub(self, from_edge):
        best_hub = None
        best_walk = None
        best_time = None

        for hub in self.uam_hubs:
            if from_edge == hub["access_edge"]:
                return hub, None

            walk = find_intermodal_route_from_edges(
                from_edge,
                hub["access_edge"],
                0,
                modes='public',
            )
            if not self._is_valid_intermodal_route(walk, from_edge, hub["access_edge"]):
                continue

            travel_time = sum(stage.travelTime for stage in walk)
            if best_time is None or travel_time < best_time:
                best_hub = hub
                best_walk = walk
                best_time = travel_time

        return best_hub, best_walk

    def _best_egress_hub(self, to_edge):
        best_hub = None
        best_walk = None
        best_time = None

        for hub in self.uam_hubs:
            if hub["access_edge"] == to_edge:
                return hub, None

            walk = find_intermodal_route_from_edges(
                hub["access_edge"],
                to_edge,
                0,
                modes='public',
            )
            if not self._is_valid_intermodal_route(walk, hub["access_edge"], to_edge):
                continue

            travel_time = sum(stage.travelTime for stage in walk)
            if best_time is None or travel_time < best_time:
                best_hub = hub
                best_walk = walk
                best_time = travel_time

        return best_hub, best_walk

    def branch_by_execution(self, vehicle, from_location, to_location, arrival_time):
        if vehicle.execution_kind == ExecutionKind.VEHICLE:
            return self._get_vehicle_possible_route(vehicle, from_location, to_location)

        if vehicle.execution_kind == ExecutionKind.PEDESTRIAN:
            return self._get_pedestrian_possible_route(vehicle, from_location, to_location)

        if vehicle.execution_kind == ExecutionKind.INTERMODAL:
            return self._get_intermodal_possible_route(vehicle, from_location, to_location, arrival_time)

        if vehicle.execution_kind == ExecutionKind.TAXI:
            return self._get_taxi_possible_route(vehicle, from_location, to_location)

        raise ValueError(f"Unsupported execution kind: {vehicle.execution_kind}")

    def _get_vehicle_possible_route(self, vehicle, from_location, to_location):
        v_class = self.get_v_class(vehicle)
        return self._build_road_route(vehicle, from_location, to_location, v_class)

    def _build_road_route(self, vehicle, from_location, to_location, v_class):
        route = find_route(
            from_location,
            to_location,
            v_class=v_class,
            v_type=vehicle.sumo_type_id,
        )
        return PossibleRoute(vehicle, route.edges, route.travelTime, route.length)

    def _get_pedestrian_possible_route(self, vehicle, from_location, to_location):
        try:
            from_edge = get_road_edge(from_location, "pedestrian")
            to_edge = get_road_edge(to_location, "pedestrian")
            intermodal_route = find_intermodal_route_from_edges(from_edge, to_edge, -1, modes="")
            if not intermodal_route:
                return PossibleRoute(vehicle, None, None, None)

            if not all(getattr(stage, "type", None) == 2 for stage in intermodal_route):
                return PossibleRoute(vehicle, None, None, None)

            collapsed_edges = []
            for stage in intermodal_route:
                stage_edges = list(getattr(stage, "edges", []) or [])
                if not stage_edges:
                    continue
                if collapsed_edges and collapsed_edges[-1] == stage_edges[0]:
                    collapsed_edges.extend(stage_edges[1:])
                else:
                    collapsed_edges.extend(stage_edges)

            travel_time = sum(getattr(stage, "travelTime", 0) or 0 for stage in intermodal_route)
            distance = sum(getattr(stage, "length", 0) or 0 for stage in intermodal_route)
            return PossibleRoute(vehicle, collapsed_edges, travel_time, distance)
        except Exception as exc:
            log_debug(f"[PEDESTRIAN_ROUTE] Intermodal walk lookup failed: {exc}")
            return PossibleRoute(vehicle, None, None, None)

    def _get_intermodal_possible_route(self, vehicle, from_location, to_location, arrival_time):
        route = find_intermodal_route(from_location, to_location, arrival_time, modes='public')
        if not route:
            return PossibleRoute(vehicle, None, None, None)
        pt = False
        for stage in route:
            if stage.type != 2: pt = True
            elif getattr(stage, "line", None): pt = True
            elif getattr(stage, "destStop", None): pt = True
        if not pt: return PossibleRoute(vehicle, None, None, None)

        travel_time = sum(stage.travelTime for stage in route)
        length = sum(stage.length for stage in route)
        from_edge = route[0].edges[0]
        to_edge = route[-1].edges[-1]
        return PossibleRoute(vehicle, [from_edge, to_edge], travel_time, length)

    def _get_taxi_possible_route(self, vehicle, from_location, to_location):
        if vehicle.taxi_line == "taxi:air":
            return self._get_air_taxi_possible_route(vehicle, from_location, to_location)
        return PossibleRoute(vehicle, None, None, None)

    def _get_air_taxi_possible_route(self, vehicle, from_location, to_location):
        if len(self.uam_hubs) < 2:
            return PossibleRoute(vehicle, None, None, None)

        pedestrian_from_edge = get_road_edge(from_location, "pedestrian")
        pedestrian_to_edge = get_road_edge(to_location, "pedestrian")
        if pedestrian_from_edge is None or pedestrian_to_edge is None:
            return PossibleRoute(vehicle, None, None, None)

        start_hub, access_walk = self._best_access_hub(pedestrian_from_edge)
        end_hub, egress_walk = self._best_egress_hub(pedestrian_to_edge)
        if start_hub is None or end_hub is None:
            return PossibleRoute(vehicle, None, None, None)
        if start_hub["id"] == end_hub["id"]:
            return PossibleRoute(vehicle, None, None, None)

        hub_boarding_walk = None
        if start_hub["access_edge"] != start_hub["taxi_edge"]:
            hub_boarding_walk = find_intermodal_route_from_edges(
                start_hub["access_edge"],
                start_hub["taxi_edge"],
                0,
                modes='',
            )
            if not self._is_valid_walk_route(hub_boarding_walk, start_hub["access_edge"], start_hub["taxi_edge"]):
                return PossibleRoute(vehicle, None, None, None)

        hub_alighting_walk = None
        if end_hub["taxi_edge"] != end_hub["access_edge"]:
            hub_alighting_walk = find_intermodal_route_from_edges(
                end_hub["taxi_edge"],
                end_hub["access_edge"],
                0,
                modes='',
            )
            if not self._is_valid_walk_route(hub_alighting_walk, end_hub["taxi_edge"], end_hub["access_edge"]):
                return PossibleRoute(vehicle, None, None, None)

        air_distance = self._euclidean_distance(start_hub["xy"], end_hub["xy"])
        air_time = air_distance / self.air_taxi_speed_mps if self.air_taxi_speed_mps > 0 else 0
        travel_time = (
            (sum(stage.travelTime for stage in access_walk) if access_walk else 0)
            + (sum(stage.travelTime for stage in hub_boarding_walk) if hub_boarding_walk else 0)
            + self.air_taxi_wait_time_s
            + self.air_taxi_pickup_time_s
            + air_time
            + self.air_taxi_dropoff_time_s
            + (sum(stage.travelTime for stage in hub_alighting_walk) if hub_alighting_walk else 0)
            + (sum(stage.travelTime for stage in egress_walk) if egress_walk else 0)
        )
        stage_plan = []
        if access_walk is not None:
            stage_plan.extend(self._summarise_access_stage(access_walk, pedestrian_from_edge, start_hub["access_edge"]))
        if hub_boarding_walk is not None:
            stage_plan.extend({
                "kind": "walk",
                "edges": list(stage.edges),
            } for stage in hub_boarding_walk)
        details = {
            "taxi_line": vehicle.taxi_line,
            "start_hub_id": start_hub["id"],
            "end_hub_id": end_hub["id"],
            "access_uses_pt": self._intermodal_uses_public_transport(access_walk),
            "egress_uses_pt": self._intermodal_uses_public_transport(egress_walk),
            "stage_plan": stage_plan + [
                {
                    "kind": "ride",
                    "from_edge": start_hub["taxi_edge"],
                    "to_edge": end_hub["taxi_edge"],
                    "lines": vehicle.taxi_line,
                },
            ] + ([{
                    "kind": "walk",
                    "edges": list(stage.edges),
                } for stage in hub_alighting_walk] if hub_alighting_walk is not None else []) + (
                    self._summarise_access_stage(egress_walk, end_hub["access_edge"], pedestrian_to_edge)
                    if egress_walk is not None else []
                ),
        }
        distance = (
            (sum(stage.length for stage in access_walk) if access_walk else 0)
            + (sum(stage.length for stage in hub_boarding_walk) if hub_boarding_walk else 0)
            + air_distance
            + (sum(stage.length for stage in hub_alighting_walk) if hub_alighting_walk else 0)
            + (sum(stage.length for stage in egress_walk) if egress_walk else 0)
        )
        if access_walk is not None:
            route_start_edge = access_walk[0].edges[0]
        elif hub_boarding_walk is not None:
            route_start_edge = hub_boarding_walk[0].edges[0]
        else:
            route_start_edge = start_hub["taxi_edge"]
        if egress_walk is not None:
            route_end_edge = egress_walk[-1].edges[-1]
        elif hub_alighting_walk is not None:
            route_end_edge = hub_alighting_walk[-1].edges[-1]
        else:
            route_end_edge = end_hub["taxi_edge"]
        return PossibleRoute(
            vehicle,
            [route_start_edge, route_end_edge],
            travel_time,
            distance,
            details=details,
        )

    def add_traffic_participant(self, route):
        vehicle = self._resolve_route_vehicle(route)

        if vehicle.execution_kind == ExecutionKind.VEHICLE:
            if self.get_v_class(vehicle) == "taxi":
                return self._add_taxi_participant(route, vehicle)
            return self._add_vehicle_participant(route, vehicle)
        if vehicle.execution_kind == ExecutionKind.PEDESTRIAN:
            return self._add_pedestrian_participant(route, vehicle)
        if vehicle.execution_kind == ExecutionKind.INTERMODAL:
            return self._add_intermodal_participant(route, vehicle)
        if vehicle.execution_kind == ExecutionKind.TAXI:
            return self._add_taxi_participant(route, vehicle)
        raise Exception(f'Not implemented execution kind was chosen: {vehicle.execution_kind}')

    def _resolve_route_vehicle(self, route):
        vehicle_data = route['vehicle']
        return vehicle_data if hasattr(vehicle_data, 'execution_kind') else self.vehicle_registry.get(vehicle_data['name'])

    def _add_vehicle_participant(self, route, vehicle):
        return add_vehicle_route(route, vehicle.sumo_type_id)

    def _add_pedestrian_participant(self, route, vehicle):
        return add_pedestrian(route, vehicle.sumo_type_id)

    def _add_intermodal_participant(self, route, vehicle):
        return add_intermodal(route, vehicle.sumo_type_id)

    def _add_taxi_participant(self, route, vehicle):
        return add_stage_plan(route)

    def get_building_categories_string(self):
        return ', '.join(self.building_categories)
