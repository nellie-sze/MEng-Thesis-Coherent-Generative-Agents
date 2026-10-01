import functools
import os
from pathlib import Path
import xml.etree.ElementTree as ET

from util.logging import log_error_without_trace


@functools.lru_cache(maxsize=None)
def _load_net(net_file):
    net_path = Path(net_file)
    if not net_path.is_absolute():
        net_path = Path(__file__).resolve().parents[2] / net_path
    root = ET.parse(net_path.resolve()).getroot()
    special_edges = {
        edge.attrib["id"]
        for edge in root.findall("edge")
        if edge.attrib.get("function") == "internal" or edge.attrib["id"].startswith(":")
    }
    incoming_by_internal_edge = {}
    outgoing_by_internal_edge = {}
    for connection in root.findall("connection"):
        from_edge = connection.attrib.get("from")
        to_edge = connection.attrib.get("to")
        via = connection.attrib.get("via", "")
        if via.startswith(":"):
            internal_edge = via.rsplit("_", 1)[0]
            incoming_by_internal_edge.setdefault(internal_edge, []).append(from_edge)
        if from_edge and from_edge.startswith(":"):
            outgoing_by_internal_edge.setdefault(from_edge, []).append(to_edge)
    return special_edges, incoming_by_internal_edge, outgoing_by_internal_edge


def _resolve_non_internal_edge(edge_id, net_file, prefer_outgoing):
    if not net_file or not edge_id or not edge_id.startswith(':'):
        return edge_id

    special_edges, incoming_by_internal_edge, outgoing_by_internal_edge = _load_net(net_file)
    connected_edges = outgoing_by_internal_edge.get(edge_id, []) if prefer_outgoing else incoming_by_internal_edge.get(edge_id, [])
    for candidate in connected_edges:
        if candidate and candidate not in special_edges and not candidate.startswith(':'):
            return candidate

    fallback_edges = incoming_by_internal_edge.get(edge_id, []) + outgoing_by_internal_edge.get(edge_id, [])
    for candidate in fallback_edges:
        if candidate and candidate not in special_edges and not candidate.startswith(':'):
            return candidate

    return edge_id


def generate_trips_xml(route_descriptions, net_file=None):
    route_descriptions.sort(key=lambda x: x['departure_time'])
    trips_xml = ('<routes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                 'xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/routes_file.xsd">\n')
    uam_trips_xml = ('<routes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                     'xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/routes_file.xsd">\n')
    for route_description in route_descriptions:
        route_xml, is_uam_trip = convert_to_trip_xml(route_description, net_file)
        if route_xml is not None:
            if is_uam_trip:
                uam_trips_xml += route_xml
            else:
                trips_xml += route_xml
    trips_xml += '</routes>\n'
    uam_trips_xml += '</routes>\n'
    return trips_xml, uam_trips_xml


def merge_trips_xml(*xml_documents):
    routes_body = []
    header = ('<routes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
              'xsi:noNamespaceSchemaLocation="http://sumo.dlr.de/xsd/routes_file.xsd">\n')
    for xml_document in xml_documents:
        if not xml_document:
            continue
        lines = xml_document.splitlines(keepends=True)
        if len(lines) <= 2:
            continue
        routes_body.extend(lines[1:-1])
    return header + ''.join(routes_body) + '</routes>\n'


def _merge_consecutive_walk_stages(stage_plan):
    merged = []
    current_walk_edges = []

    for stage in stage_plan:
        if stage["kind"] == "walk":
            edges = list(stage.get("edges") or [])
            if not edges:
                continue
            if not current_walk_edges:
                current_walk_edges = edges
            elif current_walk_edges[-1] == edges[0]:
                current_walk_edges.extend(edges[1:])
            else:
                current_walk_edges.extend(edges)
        else:
            if current_walk_edges:
                merged.append({"kind": "walk", "edges": current_walk_edges})
                current_walk_edges = []
            merged.append(stage)

    if current_walk_edges:
        merged.append({"kind": "walk", "edges": current_walk_edges})

    return merged


def convert_stage_plan_to_trip_xml(trip_id, departure_time, stage_plan, net_file=None):
    stage_plan = _merge_consecutive_walk_stages(stage_plan)
    trip = f'\t<person id="{trip_id}" depart="{departure_time}">\n'
    for stage in stage_plan:
        if stage['kind'] == 'walk':
            from_edge = _resolve_non_internal_edge(stage['edges'][0], net_file, prefer_outgoing=False)
            to_edge = _resolve_non_internal_edge(stage['edges'][-1], net_file, prefer_outgoing=True)
            trip += f'\t\t<walk from="{from_edge}" to="{to_edge}" arrivalPos="random"/>\n'
        elif stage['kind'] == 'personTrip':
            from_edge = _resolve_non_internal_edge(stage["from_edge"], net_file, prefer_outgoing=False)
            to_edge = _resolve_non_internal_edge(stage["to_edge"], net_file, prefer_outgoing=True)
            trip += (
                f'\t\t<personTrip from="{from_edge}" to="{to_edge}" '
                f'modes="{stage.get("modes", "public")}"/>\n'
            )
        elif stage['kind'] == 'ride':
            from_edge = _resolve_non_internal_edge(stage["from_edge"], net_file, prefer_outgoing=False)
            to_edge = _resolve_non_internal_edge(stage["to_edge"], net_file, prefer_outgoing=True)
            trip += (
                f'\t\t<ride from="{from_edge}" to="{to_edge}" '
                f'lines="{stage.get("lines", "taxi")}"/>\n'
            )
        else:
            raise NotImplementedError(f'Unsupported stage kind "{stage["kind"]}"')
    trip += '\t</person>\n'
    return trip


def _parse_route_id(agent_id, route_id):
    route_text = str(route_id)
    leg_text = route_text[-4:].zfill(4)
    leg_index = int(leg_text[:2])
    return f"{agent_id}_{leg_index}", leg_index


def convert_to_trip_xml(route_description, net_file=None):
    try:
        agent_id = route_description['agent_id']
        route_id = route_description['route_id']
        trip_id, _ = _parse_route_id(agent_id, route_id)
        departure_time = route_description['departure_time']
        vehicle = route_description.get('vehicle')
        if vehicle is None:
            transportation = route_description['means_of_transport']
            execution_kind = 'intermodal' if transportation == 'public transport' else 'vehicle'
            sumo_type_id = route_description.get('SUMO_typeID')
            taxi_line = route_description.get('taxi_line')
            details = route_description.get('details', {})
        else:
            transportation = vehicle['name']
            execution_kind = vehicle['execution_kind']
            sumo_type_id = vehicle['SUMO_typeID']
            taxi_line = vehicle.get('taxi_line')
            details = route_description.get('details', {})
        is_uam_trip = transportation == 'air_taxi'
        from_edge = _resolve_non_internal_edge(route_description['route'][0], net_file, prefer_outgoing=False)
        to_edge = _resolve_non_internal_edge(route_description['route'][-1], net_file, prefer_outgoing=True)
        stage_plan = details.get('stage_plan') if isinstance(details, dict) else None

        if from_edge != to_edge:
            if stage_plan:
                return convert_stage_plan_to_trip_xml(trip_id, departure_time, stage_plan, net_file), is_uam_trip
            if is_uam_trip:
                trip = (f'\t<person id="{trip_id}" depart="{departure_time}">\n'
                        f'\t\t<ride from="{from_edge}" to="{to_edge}" lines="{taxi_line or "taxi:air"}"/>\n'
                        f'\t</person>\n')
                return trip, True
            elif execution_kind == 'vehicle':
                type_attr = f' type="{sumo_type_id}"' if sumo_type_id else ''
                trip = f'\t<trip id="{trip_id}"{type_attr} depart="{departure_time}" from="{from_edge}" to="{to_edge}" />\n'
                return trip, False
            elif execution_kind == 'pedestrian':
                trip = (f'\t<person id="{trip_id}" depart="{departure_time}">\n'
                        f'\t\t<walk from="{from_edge}" to="{to_edge}" arrivalPos="random"/>\n'
                        f'\t</person>\n')
                return trip, False
            elif execution_kind == 'intermodal':
                modes = 'public'
                trip = (f'\t<person id="{trip_id}" depart="{departure_time}">\n'
                        f'\t\t<personTrip from="{from_edge}" to="{to_edge}" modes="{modes}"/>\n'
                        f'\t</person>\n')
                return trip, False
            else:
                raise NotImplementedError(f'Transportation type "{transportation}" not implemented')
    except Exception as exc:
        log_error_without_trace(
            f"[TRIPS] Skipping malformed route description for agent_id={route_description.get('agent_id')} "
            f"route_id={route_description.get('route_id')!r}: {exc}"
        )
        return None, False
    return None, False
