from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import sumolib


REPO_ROOT = Path(__file__).resolve().parents[2]
UAM_SUMO_ROOT = REPO_ROOT / "uam-sumo"
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from config import sim_config  # noqa: E402

DEFAULT_SIM_CONFIG_NAME = "config_wedding_sumo"
DEFAULT_HUB_COORDINATES: list[tuple[float, float]] = [
    (3000, 3000),  
    (3400, 3750),  
    (5150, 3550), 
    (5200, 1650),  
    (4100, 2400), 
]


def _resolve_config(sim_config_name: str) -> dict:
    if not hasattr(sim_config, sim_config_name):
        raise AttributeError(f"Unknown sim config {sim_config_name!r} in src/config/sim_config.py")
    return getattr(sim_config, sim_config_name)


def _resolve_base_scenario_name(base_net_file: Path) -> str:
    base_scenario_name = base_net_file.stem
    if base_scenario_name.endswith(".net"):
        base_scenario_name = base_scenario_name[:-4]
    return base_scenario_name


def _build_output_paths(output_dir: Path, output_scenario_name: str) -> dict[str, Path]:
    return {
        "output_dir": output_dir,
        "net_file": output_dir / f"{output_scenario_name}.net.xml",
        "hub_add_file": output_dir / f"{output_scenario_name}_hubs.add.xml",
        "runtime_add_file": output_dir / f"{output_scenario_name}_runtime.add.xml",
        "route_file": output_dir / f"{output_scenario_name}_routes.add.xml",
        "sumocfg_file": output_dir / f"{output_scenario_name}.sumocfg",
        "hubs_file": output_dir / f"{output_scenario_name}_hubs.json",
    }


def ensure_prerequisites(base_net_file: Path, base_pt_route_file: Path, base_vtypes_file: Path | None,
                         base_pt_stops_file: Path | None) -> None:
    required_files = [
        base_net_file,
        base_pt_route_file,
        UAM_SUMO_ROOT / "createUamHubs.py",
    ]
    optional_files = [base_vtypes_file, base_pt_stops_file]
    required_files.extend(path for path in optional_files if path is not None)
    missing = [str(path) for path in required_files if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing required input files:\n" + "\n".join(missing))


def write_empty_additional_file(path: Path) -> None:
    root = ET.Element(
        "additional",
        {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/additional_file.xsd",
        },
    )
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_temp_sumocfg(path: Path, net_name: str, route_name: str, add_name: str) -> None:
    root = ET.Element("configuration")

    input_el = ET.SubElement(root, "input")
    ET.SubElement(input_el, "net-file", {"value": net_name})
    ET.SubElement(input_el, "route-files", {"value": route_name})
    ET.SubElement(input_el, "additional-files", {"value": add_name})

    time_el = ET.SubElement(root, "time")
    ET.SubElement(time_el, "begin", {"value": "0"})
    ET.SubElement(time_el, "end", {"value": "3600"})

    processing_el = ET.SubElement(root, "processing")
    ET.SubElement(processing_el, "ignore-route-errors", {"value": "true"})
    ET.SubElement(processing_el, "no-warnings", {"value": "true"})

    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def merge_additional_files(output_path: Path, inputs: list[Path]) -> None:
    merged_root = ET.Element(
        "additional",
        {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/additional_file.xsd",
        },
    )
    keyed_children: dict[tuple[str, str], ET.Element] = {}
    ordered_keys: list[tuple[str, str]] = []

    for input_path in inputs:
        tree = ET.parse(input_path)
        for child in tree.getroot():
            child_id = child.get("id")
            if not child_id:
                merged_root.append(child)
                continue
            key = (child.tag, child_id)
            if key not in keyed_children:
                ordered_keys.append(key)
            keyed_children[key] = child

    for key in ordered_keys:
        merged_root.append(keyed_children[key])

    ET.ElementTree(merged_root).write(output_path, encoding="utf-8", xml_declaration=True)


def rewrite_sumocfg(input_path: Path, output_path: Path, net_name: str, route_name: str, add_name: str) -> None:
    tree = ET.parse(input_path)
    root = tree.getroot()
    root.find(".//net-file").set("value", net_name)
    root.find(".//route-files").set("value", route_name)
    root.find(".//additional-files").set("value", add_name)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def remove_generated_uamtaxi_vtype(input_path: Path, output_path: Path) -> None:
    tree = ET.parse(input_path)
    root = tree.getroot()
    for child in list(root):
        if child.tag == "vType" and child.get("id") == "uamtaxi":
            root.remove(child)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def sync_generated_uamtaxi_vtype(source_route_path: Path, runtime_add_path: Path) -> None:
    route_root = ET.parse(source_route_path).getroot()
    generated_uamtaxi = next(
        (child for child in route_root if child.tag == "vType" and child.get("id") == "uamtaxi"),
        None,
    )
    if generated_uamtaxi is None:
        return

    runtime_tree = ET.parse(runtime_add_path)
    runtime_root = runtime_tree.getroot()
    for child in list(runtime_root):
        if child.tag == "vType" and child.get("id") == "uamtaxi":
            runtime_root.remove(child)
    runtime_root.insert(0, generated_uamtaxi)
    runtime_tree.write(runtime_add_path, encoding="utf-8", xml_declaration=True)


def find_nearest_access_edge(net, point_xy: tuple[float, float], v_class: str = "pedestrian",
                             initial_radius: float = 50, max_multiplier: int = 20) -> str | None:
    x, y = point_xy
    for multiplier in range(1, max_multiplier + 1):
        nearby_edges = net.getNeighboringEdges(x, y, initial_radius * multiplier, includeJunctions=False)
        if not nearby_edges:
            continue
        for edge, _ in sorted(nearby_edges, key=lambda item: item[1]):
            if edge.isSpecial():
                continue
            if edge.allows(v_class):
                return edge.getID()
    return None


def find_hub_connector_edge(net, hub_index: int) -> str | None:
    connector_prefix = f"hub_con_{hub_index * 2}_"
    connector_candidates = sorted(
        edge.getID() for edge in net.getEdges() if edge.getID().startswith(connector_prefix)
    )
    if connector_candidates:
        return connector_candidates[0]
    return None


def find_hub_taxi_edge(net, hub_index: int) -> str | None:
    taxi_edge_id = f"uam_{hub_index * 2}_{hub_index * 2 + 1}"
    try:
        net.getEdge(taxi_edge_id)
    except KeyError:
        return None
    return taxi_edge_id


def read_net_offset(net_file: Path) -> tuple[float, float]:
    tree = ET.parse(net_file)
    location = tree.getroot().find("location")
    if location is None:
        raise RuntimeError(f"Could not find <location> in generated net file: {net_file}")
    net_offset = location.get("netOffset")
    if not net_offset:
        raise RuntimeError(f"Generated net file is missing netOffset: {net_file}")
    x_offset, y_offset = (float(value) for value in net_offset.split(","))
    return x_offset, y_offset


def build_hub_metadata(net_file: Path, hub_coordinates: list[tuple[float, float]]) -> list[dict[str, object]]:
    net = sumolib.net.readNet(str(net_file))
    net_offset_x, net_offset_y = read_net_offset(net_file)
    hubs_payload = []
    for index, (sumo_x, sumo_y) in enumerate(hub_coordinates):
        hub_id = f"hub_{index}"
        access_edge = find_hub_connector_edge(net, index)
        taxi_edge = find_hub_taxi_edge(net, index)
        if access_edge is None:
            message = (
                f"Unable to resolve pedestrian connector edge for UAM hub {hub_id} at ({sumo_x}, {sumo_y}) "
                f"in generated network {net_file}. Expected a generated edge named like "
                f"'hub_con_{index * 2}_*'. Move the hub closer to a walkable edge and rebuild."
            )
            print(f"ERROR: {message}")
            raise RuntimeError(message)
        if taxi_edge is None:
            message = (
                f"Unable to resolve taxi hub edge for UAM hub {hub_id} at ({sumo_x}, {sumo_y}) "
                f"in generated network {net_file}. Expected generated edge '{'uam_%s_%s' % (index * 2, index * 2 + 1)}'."
            )
            print(f"ERROR: {message}")
            raise RuntimeError(message)
        connector_edge = net.getEdge(access_edge)
        if not connector_edge.allows("pedestrian"):
            message = (
                f"Generated access edge {access_edge} for UAM hub {hub_id} does not allow pedestrians."
            )
            print(f"ERROR: {message}")
            raise RuntimeError(message)
        taxi_net_edge = net.getEdge(taxi_edge)
        if not taxi_net_edge.allows("taxi"):
            message = (
                f"Generated taxi edge {taxi_edge} for UAM hub {hub_id} does not allow taxis."
            )
            print(f"ERROR: {message}")
            raise RuntimeError(message)
        projected_x = sumo_x - net_offset_x
        projected_y = sumo_y - net_offset_y
        hubs_payload.append({
            "id": hub_id,
            "x": projected_x,
            "y": projected_y,
            "access_edge": access_edge,
            "taxi_edge": taxi_edge,
            "sumo_x": sumo_x,
            "sumo_y": sumo_y,
        })
    return hubs_payload


def build_uam_scenario(
    sim_config_name: str = DEFAULT_SIM_CONFIG_NAME,
    hub_coordinates: list[tuple[float, float]] | None = None,
    output_dir: Path | None = None,
    output_scenario_name: str | None = None,
    quiet: bool = False,
) -> dict[str, str]:
    active_config = _resolve_config(sim_config_name)
    base_net_file = REPO_ROOT / active_config["net_file"]
    base_vtypes_file = REPO_ROOT / active_config["v_types_file"] if active_config.get("v_types_file") else None
    base_pt_stops_file = REPO_ROOT / active_config["pt_stops_file"] if active_config.get("pt_stops_file") else None
    base_pt_route_file = REPO_ROOT / active_config["pt_vehicles_file"]

    base_scenario_name = _resolve_base_scenario_name(base_net_file)
    resolved_output_scenario_name = output_scenario_name or f"{base_scenario_name}_uam"
    resolved_output_dir = output_dir or (REPO_ROOT / "data" / "open_street_map" / resolved_output_scenario_name)
    output_paths = _build_output_paths(resolved_output_dir, resolved_output_scenario_name)
    resolved_hub_coordinates = hub_coordinates or DEFAULT_HUB_COORDINATES

    ensure_prerequisites(base_net_file, base_pt_route_file, base_vtypes_file, base_pt_stops_file)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    hub_count = len(resolved_hub_coordinates)
    coordinate_args = [str(value) for pair in resolved_hub_coordinates for value in pair]

    with tempfile.TemporaryDirectory(prefix="uam_build_", dir=resolved_output_dir) as temp_dir_name:
        temp_dir = Path(temp_dir_name)

        temp_net = temp_dir / base_net_file.name
        temp_route = temp_dir / base_pt_route_file.name
        temp_add = temp_dir / "base_uam.add.xml"
        temp_sumocfg = temp_dir / "base_uam.sumocfg"

        shutil.copy2(base_net_file, temp_net)
        shutil.copy2(base_pt_route_file, temp_route)
        write_empty_additional_file(temp_add)
        write_temp_sumocfg(temp_sumocfg, temp_net.name, temp_route.name, temp_add.name)

        command = [
            sys.executable,
            str(UAM_SUMO_ROOT / "createUamHubs.py"),
            str(temp_sumocfg),
            *coordinate_args,
        ]
        completed = subprocess.run(
            command,
            cwd=UAM_SUMO_ROOT,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "createUamHubs.py failed.\n"
                f"Command: {command}\n"
                f"Return code: {completed.returncode}\n"
                f"STDOUT:\n{completed.stdout}\n"
                f"STDERR:\n{completed.stderr}"
            )
        if not quiet and completed.stdout:
            print(completed.stdout, end="")
        if not quiet and completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)

        generated_net = temp_dir / f"{hub_count}_uam_hubs_{temp_net.name}"
        generated_route = temp_dir / f"{hub_count}_uam_hubs_{temp_route.name}"
        generated_add = temp_dir / f"{hub_count}_uam_hubs_{temp_add.name}"
        generated_sumocfg = temp_dir / f"{hub_count}_uam_hubs_{temp_sumocfg.name}"

        for generated_path in [generated_net, generated_route, generated_add, generated_sumocfg]:
            if not generated_path.exists():
                raise FileNotFoundError(f"Expected generated file not found: {generated_path}")

        shutil.copy2(generated_net, output_paths["net_file"])
        remove_generated_uamtaxi_vtype(generated_route, output_paths["route_file"])
        shutil.copy2(generated_add, output_paths["hub_add_file"])
        rewrite_sumocfg(
            generated_sumocfg,
            output_paths["sumocfg_file"],
            output_paths["net_file"].name,
            output_paths["route_file"].name,
            output_paths["runtime_add_file"].name,
        )
        merge_additional_files(
            output_paths["runtime_add_file"],
            [path for path in [base_vtypes_file, base_pt_stops_file, output_paths["hub_add_file"]] if path is not None],
        )
        sync_generated_uamtaxi_vtype(generated_route, output_paths["runtime_add_file"])

    hubs_payload = build_hub_metadata(output_paths["net_file"], resolved_hub_coordinates)
    output_paths["hubs_file"].write_text(json.dumps(hubs_payload, indent=2), encoding="utf-8")

    print("Built UAM scenario artefacts:")
    print(f"  source_config:   {sim_config_name}")
    print(f"  net_file:        {output_paths['net_file']}")
    print(f"  runtime_add:     {output_paths['runtime_add_file']}")
    print(f"  hub_additional:  {output_paths['hub_add_file']}")
    print(f"  route_file:      {output_paths['route_file']}")
    print(f"  sumocfg:         {output_paths['sumocfg_file']}")
    print(f"  hub_metadata:    {output_paths['hubs_file']}")
    print("\nSuggested runtime config overrides:")
    print(f"  net_file = '{output_paths['net_file'].relative_to(REPO_ROOT).as_posix()}'")
    print(f"  v_types_file = '{output_paths['runtime_add_file'].relative_to(REPO_ROOT).as_posix()}'")
    print("  pt_stops_file = ''")
    print(f"  uam_hubs_file = '{output_paths['hubs_file'].relative_to(REPO_ROOT).as_posix()}'")
    return {
        "source_config": sim_config_name,
        "net_file": str(output_paths["net_file"]),
        "runtime_add_file": str(output_paths["runtime_add_file"]),
        "hub_additional_file": str(output_paths["hub_add_file"]),
        "route_file": str(output_paths["route_file"]),
        "sumocfg_file": str(output_paths["sumocfg_file"]),
        "hubs_file": str(output_paths["hubs_file"]),
    }


def _parse_hub_coordinates(raw_values: list[str]) -> list[tuple[float, float]]:
    if len(raw_values) % 2 != 0:
        raise ValueError("Hub coordinates must be provided as x y pairs.")
    coordinates = []
    for index in range(0, len(raw_values), 2):
        coordinates.append((float(raw_values[index]), float(raw_values[index + 1])))
    return coordinates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a UAM-ready SUMO scenario from a sim_config entry.")
    parser.add_argument("--sim-config", default=DEFAULT_SIM_CONFIG_NAME,
                        help="Name of the config object in src/config/sim_config.py")
    parser.add_argument("--hub-coordinates-file",
                        help="JSON file containing a list of [x, y] coordinate pairs.")
    parser.add_argument("--hub-coordinates", nargs="*",
                        help="Inline hub coordinates as x y x y ...")
    parser.add_argument("--output-dir", help="Output directory for generated artefacts.")
    parser.add_argument("--output-scenario-name", help="Output scenario stem/name.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.hub_coordinates_file:
        hub_coordinates = [tuple(pair) for pair in json.loads(Path(args.hub_coordinates_file).read_text(encoding="utf-8"))]
    elif args.hub_coordinates:
        hub_coordinates = _parse_hub_coordinates(args.hub_coordinates)
    else:
        hub_coordinates = DEFAULT_HUB_COORDINATES
    build_uam_scenario(
        sim_config_name=args.sim_config,
        hub_coordinates=hub_coordinates,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        output_scenario_name=args.output_scenario_name,
    )
