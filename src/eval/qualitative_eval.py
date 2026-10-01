import argparse
import json
import random
from pprint import pprint

import folium
from pyproj import Transformer
from eval.results_access.simulation_results import SimulationResults

# ============================================================
# Coordinate transformer (Berlin UTM → WGS84)
# ============================================================
TRANSFORMER = Transformer.from_crs("EPSG:25833", "EPSG:4326", always_xy=True)

# Colors per modality on the map
MODE_COLORS = {
    "pedestrian": "green",
    "bicycle": "blue",
    "passenger": "red",
    "public transport": "purple",
    "unknown": "gray",
}

SIM = None


# ============================================================
# Utility functions
# ============================================================
def safe_parse(item):
    if isinstance(item, str):
        try:
            return json.loads(item)
        except json.JSONDecodeError:
            return item
    return item


def get_location_label(loc):
    if not isinstance(loc, dict):
        return str(loc)
    b = loc.get("building", {})
    name = loc.get("name")
    if name:
        return name

    params = b.get("parameters")
    if isinstance(params, list) and params:
        return params[0]

    btype = b.get("type")
    if btype:
        return btype

    return "(unknown)"


def to_wgs84(loc):
    if not isinstance(loc, dict):
        return None
    if "x" in loc and "y" in loc:
        lon, lat = TRANSFORMER.transform(loc["x"], loc["y"])
        return f"{lat:.6f}, {lon:.6f}"
    return None


def to_wgs84_tuple(loc):
    """Returns (lat, lon) for Folium."""
    if not isinstance(loc, dict):
        return None
    if "x" in loc and "y" in loc:
        lon, lat = TRANSFORMER.transform(loc["x"], loc["y"])
        return lat, lon
    return None


# ============================================================
# Plotting function (from script 2)
# ============================================================
def plot_agent_day(agent, output_file="agent_daymap.html"):
    location_changes = agent.get("location_changes", [])
    if not location_changes:
        print("No location changes found.")
        return

    first_loc = location_changes[0]["from"]["building"]["location"]
    fmap = folium.Map(location=to_wgs84_tuple(first_loc), zoom_start=13, tiles="OpenStreetMap")

    for i, lc in enumerate(location_changes):
        src = lc["from"]["building"]["location"]
        dst = lc["to"]["building"]["location"]
        src_latlon = to_wgs84_tuple(src)
        dst_latlon = to_wgs84_tuple(dst)

        task = lc["to"]["task"]
        purpose = task.get("action")
        purpose_type = task.get("building_type")
        purpose_time = task.get("time")

        decision = lc.get("decision")
        mode = decision.get("means_of_transport", "unknown") if decision else "unknown"

        # Start marker
        folium.Marker(
            src_latlon,
            popup=f"Start of leg {i+1}",
            icon=folium.Icon(color="gray", icon="circle"),
        ).add_to(fmap)

        # Destination marker
        folium.Marker(
            dst_latlon,
            popup=f"""
            <b>Leg {i+1}</b><br>
            Purpose: {purpose}<br>
            Type: {purpose_type}<br>
            Time: {purpose_time}<br>
            Mode: {mode}
            """,
            icon=folium.Icon(color="black", icon="info-sign"),
        ).add_to(fmap)

        # Line
        folium.PolyLine(
            locations=[src_latlon, dst_latlon],
            color=MODE_COLORS.get(mode, "black"),
            weight=5,
            opacity=0.7,
            tooltip=f"{mode} [{purpose_time}]",
        ).add_to(fmap)

    fmap.save(output_file)
    print(f"Day map saved to {output_file}")


# ============================================================
# Agent printing (unchanged)
# ============================================================
def print_agent_overview(agent):
    print("\n==================== AGENT OVERVIEW ====================")

    print(f"\n[AGENT ID]\n  {agent.get('id', 'N/A')}")

    print("\n[DESCRIPTION]")
    pprint(agent.get("description", {}), indent=2)

    print("\n[DAY SCHEDULE]")
    raw_schedule = agent.get("day_schedule", {})
    if isinstance(raw_schedule, str):
        try:
            raw_schedule = json.loads(raw_schedule)
        except:
            print("Could not parse schedule.")
            raw_schedule = {}

    task_list = raw_schedule.get("task_list", [])
    if isinstance(task_list, str):
        try:
            task_list = json.loads(task_list)
        except:
            print("Task list unparsable.")

    for entry in task_list:
        entry = safe_parse(entry)
        if isinstance(entry, dict):
            print(f" - {entry.get('time', '?')}: {entry.get('action', 'unknown')} [{entry.get('building_type', '')}]")
        else:
            print(f" - {entry}")

    print("\n[LOCATION CHANGES]")
    for i, lc in enumerate(agent.get("location_changes", [])):
        lc = safe_parse(lc)
        print(f"\n  Location Change {i+1}:")
        if not isinstance(lc, dict):
            print("  Unstructured:", lc)
            continue

        src = lc.get("from", {})
        dst = lc.get("to", {})

        task = dst.get("task", {})
        print(f"    Purpose: {task.get('action')} [{task.get('building_type')} @ {task.get('time')}]")

        print(f"    From: {get_location_label(src)}")
        print(f"    To:   {get_location_label(dst)}")

        print(f"    From coordinates: {to_wgs84(src.get('building', {}).get('location'))}")
        print(f"    To coordinates:   {to_wgs84(dst.get('building', {}).get('location'))}")

        possible = lc.get("possible_routes", [])
        if possible:
            print("    Possible routes:")
            for route in possible:
                route = safe_parse(route)
                print(f"       - {route.get('means_of_transport')} ({route.get('distance')} m)")

        decision = lc.get("decision")
        print("    DECISION:" if decision else "    No decision.")
        if decision:
            pprint(decision, indent=8)


# ============================================================
# Main exploration + plotting integration
# ============================================================
def load_results(folder):
    global SIM
    SIM = SimulationResults(folder)


def explore_example_agent(agent_id=None):
    global SIM
    if SIM is None:
        raise RuntimeError("Simulation data not loaded.")

    if agent_id is None:
        agent = random.choice(SIM.agents_with_routes)
        print("Selected random agent.")
    else:
        agent = [a for a in SIM.agents_with_routes
                 if int(a["seed"]["additional_data"]["Household Person ID"]) == agent_id]
        if not agent:
            raise ValueError(f"No agent found for ID {agent_id}")
        agent = agent[0]
        print(f"Selected agent with ID {agent_id}")

    # Print overview
    print_agent_overview(agent)

    # Ask user if they want to plot
    ans = input("\nPlot this agent's day on a map? [y/N]: ").strip().lower()
    if ans == "y":
        # choose output filename
        agent_label = agent["seed"]["additional_data"]["Household Person ID"]
        fn = f"agent_{agent_label}_daymap.html"
        plot_agent_day(agent, fn)


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explore and optionally map simulated agents.")
    parser.add_argument("result_folder", help="Direct simulation result folder to load")
    args = parser.parse_args()

    load_results(args.result_folder)

    print("Type ENTER for a random agent, an ID for a specific agent, or 'q' to quit.")

    while True:
        cmd = input("> ").strip()
        if cmd.lower() in ("q", "quit", "exit"):
            break
        if cmd == "":
            explore_example_agent()
        else:
            try:
                explore_example_agent(int(cmd))
            except Exception as e:
                print("Error:", e)
