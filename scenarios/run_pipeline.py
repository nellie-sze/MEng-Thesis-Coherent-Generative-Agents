from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


SCENARIOS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCENARIOS_ROOT.parent
SRC_ROOT = REPO_ROOT / "src"
UAM_BUILD_ROOT = REPO_ROOT / "scripts" / "uam"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
if str(UAM_BUILD_ROOT) not in sys.path:
    sys.path.insert(0, str(UAM_BUILD_ROOT))

os.environ.setdefault("SUMO_HOME", str(REPO_ROOT / "sumo-1.26.0"))

from build_uam_scenario import build_uam_scenario  # noqa: E402
from config import sim_config  # noqa: E402
from model.daily_context import load_day_config  # noqa: E402
from multi_day_resume import continue_multi_day_from_state, resume_day  # noqa: E402
from multi_day_runner import prepare_next_day, prepare_simulation, run_day_from_state  # noqa: E402
from module.action.sumo.traci_wrapper import generate_uam_fleet_file  # noqa: E402
from traffic_simulacra import ROUTES_POSTFIX  # noqa: E402
from multi_day_runner import run_multi_day  # noqa: E402
from util.logging import configure_logging, log_info  # noqa: E402

from pipeline_manifest import PIPELINE_SCENARIOS  # noqa: E402

LEGACY_VALIDATION_DEFAULTS = {
    "routing_threads": 8,
    "ignore_errors": True,
    "write_trips": True,
    "repair": False,
    "ptline_routing": False,
}

LEGACY_DUA_DEFAULTS = {
    "continue_on_unbuild": True,
    "time_inc": 8640,
    "weight_memory": True,
    "pessimism": 1,
    "router_verbose": True,
    "inc_start": 0.033,
    "inc_base": 30,
    "inc_max": 1,
    "incrementation": 1,
    "time_to_teleport": 20,
    "duarouter_routing_threads": 8,
    "duarouter_ignore_errors": False,
    "include_taxi_fleet_in_dua": True,
    "routefile_mode": "routesonly",
    "output_last_route": True,
}

DEFAULT_SUMO_OUTPUTS = {
    "log": "sumo.log",
    "tripinfo": "tripinfo.xml",
    "personinfo": "personinfo.xml",
    "edge_data": "edge_data.xml",
    "collision": "collisions.xml",
    "summary": "summary-output.xml",
    "statistic": "statistic-output.xml",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a manifest-driven GTA -> validation -> DUA -> SUMO pipeline.")
    parser.add_argument("--scenario", choices=sorted(PIPELINE_SCENARIOS), help="Run a single named scenario.")
    parser.add_argument(
        "--scenarios",
        nargs="+",
        choices=sorted(PIPELINE_SCENARIOS),
        help="Run only the listed scenarios.",
    )
    parser.add_argument("--all", action="store_true", help="Run all configured scenarios sequentially.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands without executing them.")
    parser.add_argument("--resume-day", type=int, default=None, help="Resume a per-day scenario from the given day index.")
    parser.add_argument(
        "--resume-stage",
        choices=["llm", "route_generation", "postprocess"],
        default="llm",
        help="When resuming, restart from the LLM day run, the saved route-generation stage, or postprocess only.",
    )
    return parser.parse_args()


def resolve_binary(binary_name: str) -> str:
    resolved = shutil.which(binary_name)
    if resolved:
        return resolved
    binary_filename = f"{binary_name}.exe" if os.name == "nt" else binary_name
    candidates = [
        REPO_ROOT / "sumo-1.26.0" / "bin" / binary_filename,
        Path(os.environ["SUMO_HOME"]) / "bin" / binary_filename,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(
        f"Could not find {binary_name!r} on PATH or under SUMO_HOME/bundled SUMO."
    )


def resolve_duaiterate_script() -> Path:
    candidates = [
        REPO_ROOT / "sumo-1.26.0" / "tools" / "assign" / "duaIterate.py",
        Path(os.environ["SUMO_HOME"]) / "tools" / "assign" / "duaIterate.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Could not locate duaIterate.py. Checked bundled SUMO tools and SUMO_HOME.")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_pipeline_logging(output_root: Path, scenario_name: str) -> Path:
    log_path = ensure_dir(output_root) / "scenario.log"
    configure_logging(str(log_path))
    return log_path


def run_command(command: list[str], cwd: Path | None = None, dry_run: bool = False, quiet: bool = False) -> None:
    rendered = " ".join(f'"{part}"' if " " in part else part for part in command)
    location = f" (cwd={cwd})" if cwd else ""
    #log_info(f"[PIPELINE] {rendered}{location}")
    if dry_run:
        return
    subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        check=True,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None,
    )


def unique_paths(*paths: str | Path | None) -> list[Path]:
    seen = set()
    ordered = []
    for path in paths:
        if not path:
            continue
        path = Path(path)
        if str(path) not in seen:
            seen.add(str(path))
            ordered.append(path)
    return ordered


def repo_path(path: str | Path | None) -> str | None:
    if not path:
        return None
    path = Path(path)
    return str(path if path.is_absolute() else REPO_ROOT / path)


def load_manifest(name: str) -> dict:
    manifest = dict(PIPELINE_SCENARIOS[name])
    manifest["day_config_path"] = Path(manifest["day_config_path"])
    manifest["output_root"] = Path(manifest["output_root"])
    manifest["preloaded_agents_path"] = repo_path(manifest.get("preloaded_agents_path"))
    manifest["validation"] = {**LEGACY_VALIDATION_DEFAULTS, **dict(manifest.get("validation", {}))}
    manifest["dua"] = {**LEGACY_DUA_DEFAULTS, **dict(manifest.get("dua", {}))}
    manifest["sumo"] = {**dict(manifest.get("sumo", {}))}
    manifest["sumo"]["outputs"] = {**DEFAULT_SUMO_OUTPUTS, **dict(manifest["sumo"].get("outputs", {}))}
    if uam := manifest.get("uam"):
        manifest["uam"] = {
            **dict(uam),
            "hub_coordinates_path": Path(uam["hub_coordinates_path"]),
            "output_dir": Path(uam["output_dir"]),
        }
    return manifest


def resolve_preloaded_agents_path(manifest: dict) -> str | None:
    preloaded_agents_path = manifest.get("preloaded_agents_path")
    if not preloaded_agents_path:
        return None
    if Path(preloaded_agents_path).exists():
        return preloaded_agents_path
    log_info(
        f"[PIPELINE] Preloaded agents path does not exist: {preloaded_agents_path}. "
        "Continuing without preloaded agents."
    )
    return None


def maybe_build_uam(manifest: dict, dry_run: bool) -> dict[str, str] | None:
    if not (uam := manifest.get("uam")) or not uam.get("enabled") or not uam.get("build"):
        return None
    if dry_run:
        stem = uam["output_scenario_name"]
        output_dir = uam["output_dir"]
        return {
            "net_file": str(output_dir / f"{stem}.net.xml"),
            "runtime_add_file": str(output_dir / f"{stem}_runtime.add.xml"),
            "hub_additional_file": str(output_dir / f"{stem}_hubs.add.xml"),
            "route_file": str(output_dir / f"{stem}_routes.add.xml"),
            "sumocfg_file": str(output_dir / f"{stem}.sumocfg"),
            "hubs_file": str(output_dir / f"{stem}_hubs.json"),
        }
    log_info(f"[PIPELINE] Building UAM Files")
    return build_uam_scenario(
        sim_config_name=manifest["sim_config_name"],
        hub_coordinates=[tuple(pair) for pair in json.loads(uam["hub_coordinates_path"].read_text(encoding="utf-8"))],
        output_dir=uam["output_dir"],
        output_scenario_name=uam["output_scenario_name"],
        quiet=True,
    )


def build_runtime_config(manifest: dict, uam_artifacts: dict[str, str] | None) -> tuple[dict, dict]:
    runtime_config = dict(getattr(sim_config, manifest["sim_config_name"]))
    for key in ["buildings_file", "taz_file", "net_file", "poly_file", "v_types_file", "pt_stops_file", "pt_vehicles_file", "uam_hubs_file"]:
        runtime_config[key] = repo_path(runtime_config.get(key))
    runtime_config["taxi_fleet_files"] = {
        name: repo_path(path) for name, path in runtime_config.get("taxi_fleet_files", {}).items()
    }
    runtime_config["storage_path"] = str(manifest["output_root"] / "gta")
    if uam_artifacts:
        runtime_config["net_file"] = uam_artifacts["net_file"]
        runtime_config["v_types_file"] = uam_artifacts["runtime_add_file"]
        runtime_config["pt_stops_file"] = ""
        runtime_config["pt_vehicles_file"] = uam_artifacts["route_file"]
        runtime_config["uam_hubs_file"] = uam_artifacts["hubs_file"]
    static_route_files = [runtime_config.get("pt_vehicles_file")]
    return runtime_config, {"static_route_files": unique_paths(*static_route_files)}


def build_taxi_fleet_if_needed(runtime_config: dict, day_post_dir: Path, dry_run: bool = False) -> Path | None:
    taxi_fleet_sizes = runtime_config.get("taxi_fleet_sizes", {})
    fleet_size = taxi_fleet_sizes.get("air_taxi", 0)
    if fleet_size <= 0:
        return None
    fleet_path = day_post_dir / "generated_air_taxi_fleet.rou.xml"
    if dry_run:
        log_info(f"[PIPELINE] Would generate taxi fleet file at {fleet_path}")
        return fleet_path
    ensure_dir(fleet_path.parent)
    generate_uam_fleet_file(
        runtime_config["net_file"],
        str(fleet_path),
        fleet_size,
        runtime_config.get("uam_hubs_file"),
    )
    return fleet_path


def routes_file_has_entries(path: Path) -> bool:
    if not path.exists():
        return False
    contents = path.read_text(encoding="utf-8", errors="ignore")
    return "<trip " in contents or "<person " in contents


def run_validation(day_trip_path: Path, runtime_config: dict, static_route_files: list[Path], day_post_dir: Path,
                   validation_manifest: dict, dry_run: bool, output_name: str = "validated.trips.xml") -> Path:
    log_info(f"[PIPELINE] Running route validation.\n")
    validation_dir = ensure_dir(day_post_dir / "validation")
    validated_trips_path = validation_dir / output_name
    route_files = unique_paths(day_trip_path, *static_route_files)
    additional_files = unique_paths(runtime_config.get("v_types_file"), runtime_config.get("pt_stops_file"))

    command = [
        resolve_binary("duarouter"),
        "-n", str(runtime_config["net_file"]),
        "-r", ",".join(str(path) for path in route_files),
        "--routing-threads", str(validation_manifest.get("routing_threads", 1)),
        "-o", str(validated_trips_path),
        "--log", str(validation_dir / "duarouter_filter.log"),
    ]
    if additional_files:
        command.extend(["--additional-files", ",".join(str(path) for path in additional_files)])
    if validation_manifest.get("ignore_errors", True):
        command.append("--ignore-errors")
    if validation_manifest.get("repair", True):
        command.append("--repair")
    if validation_manifest.get("write_trips", True):
        command.extend(["--write-trips", "true"])
    if validation_manifest.get("ptline_routing", True) and runtime_config.get("pt_vehicles_file"):
        command.extend(["--ptline-routing", "true"])

    run_command(command, cwd=validation_dir, dry_run=dry_run)
    return validated_trips_path


def run_population_scaling(validated_trips_path: Path, runtime_config: dict, agents_path: Path, dry_run: bool,
                           trip_kind: str = "ground") -> Path:
    count = 10
    log_info(f"[PIPELINE] Running population scaling with count {count}\n")
    scaling_output_path = validated_trips_path.with_name(f"scaled_{validated_trips_path.name}")
    pt_stops_input = runtime_config.get("pt_stops_file") or ""
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "population_scaling" / "agents_replicator.py"),
        "--net-file", str(runtime_config["net_file"]),
        "--v-types-file", str(runtime_config.get("v_types_file") or ""),
        "--pt-stops-file", str(pt_stops_input),
        "--pt-vehicles-file", str(runtime_config["pt_vehicles_file"]),
        "--uam-hubs-file", str(runtime_config.get("uam_hubs_file") or ""),
        "--input-file", str(agents_path),
        "--output-file", str(scaling_output_path),
        "--count", str(count),
        "--trip-kind", str(trip_kind),
    ]
    run_command(command, cwd=REPO_ROOT / "scripts" / "population_scaling", dry_run=dry_run)
    return scaling_output_path


def calculate_dua_weights(validated_trips_path: Path, runtime_config: dict, taxi_fleet_path: Path | None, day_post_dir: Path,
                          dua_manifest: dict, dry_run: bool) -> tuple[Path, Path]:
    log_info(f"[PIPELINE] Running DUA iteration.\n")
    dua_dir = ensure_dir(day_post_dir / "dua")
    last_step = dua_manifest.get("last_step")
    trip_inputs = unique_paths(
        validated_trips_path,
        taxi_fleet_path if dua_manifest.get("include_taxi_fleet_in_dua") else None,
    )
    additional_files = unique_paths(runtime_config.get("v_types_file"), runtime_config.get("pt_stops_file"))
    command = [
        sys.executable,
        str(resolve_duaiterate_script()),
        "-n", str(runtime_config["net_file"]),
        "-t", ",".join(str(path) for path in trip_inputs),
        "-x", dua_manifest.get("routefile_mode", "routesonly"),
        "--log", str(dua_dir / "stdout.log"),
        "--dualog", str(dua_dir / "dua.log"),
        "--duarouter-routing-threads", str(dua_manifest.get("duarouter_routing_threads", 1)),
        "--time-inc", str(dua_manifest.get("time_inc", 8640)),
        "--pessimism", str(dua_manifest.get("pessimism", 1)),
        "--inc-start", str(dua_manifest.get("inc_start", 0.033)),
        "--inc-base", str(dua_manifest.get("inc_base", 30)),
        "--inc-max", str(dua_manifest.get("inc_max", 1)),
        "--incrementation", str(dua_manifest.get("incrementation", 1)),
        "--time-to-teleport", str(dua_manifest.get("time_to_teleport", 20)),
        "--sumo-ignore-route-errors", "true",
    ]
    if last_step is not None:
        # Keep the -t/--trips flag adjacent to its value; inserting at index 5
        # splits that pair and makes duaIterate parse "-t" as missing its argument.
        command[6:6] = ["-l", str(int(last_step))]
    if additional_files:
        serialized = ",".join(str(path) for path in additional_files)
        command.extend(["--additional", serialized, "--duarouter-additional-files", serialized])
    if dua_manifest.get("continue_on_unbuild", True):
        command.append("--continue-on-unbuild")
    if dua_manifest.get("weight_memory", True):
        command.append("--weight-memory")
    if dua_manifest.get("router_verbose", True):
        command.append("--router-verbose")
    if dua_manifest.get("duarouter_ignore_errors", True):
        command.append("--duarouter-ignore-errors")
    if dua_manifest.get("output_last_route", True):
        command.append("-z")

    run_command(command, cwd=dua_dir, dry_run=dry_run)
    if dry_run:
        final_step = max(int(last_step) - 1, 0) if last_step is not None else 0
        final_iteration_dir = dua_dir / f"{final_step:03d}"
        return (
            final_iteration_dir / f"vehroute_{final_step:03d}.xml",
            final_iteration_dir / "memory_dump_900.xml.gz",
        )

    route_candidates = sorted(dua_dir.glob("[0-9][0-9][0-9]/vehroute_*.xml"))
    if not route_candidates:
        route_candidates = sorted(dua_dir.glob("[0-9][0-9][0-9]/vehroute_*.xml.gz"))
    if not route_candidates:
        raise FileNotFoundError(f"Could not find final vehroute output in {dua_dir}")
    final_route_path = route_candidates[-1]
    weight_file_path = final_route_path.parent / "memory_dump_900.xml.gz"
    if not weight_file_path.exists():
        raise FileNotFoundError(f"Could not find learned DUA weights at {weight_file_path}")
    return final_route_path, weight_file_path


def apply_dua_weight(validated_trips_path: Path, dua_weight_path: Path, runtime_config: dict, day_post_dir: Path,
                     validation_manifest: dict, dry_run: bool) -> Path:
    reroute_dir = ensure_dir(day_post_dir / "final_reroute")
    final_route_path = reroute_dir / "full_weighted_routes.rou.xml"
    reroute_command = [
        resolve_binary("duarouter"),
        "-n", str(runtime_config["net_file"]),
        "-r", str(validated_trips_path),
        "--weight-files", str(dua_weight_path),
        "--routing-threads", str(validation_manifest.get("routing_threads", 1)),
        "-o", str(final_route_path),
        "--log", str(reroute_dir / "duarouter_weighted.log"),
    ]
    reroute_additional_files = unique_paths(runtime_config.get("v_types_file"), runtime_config.get("pt_stops_file"))
    if reroute_additional_files:
        reroute_command.extend(["--additional-files", ",".join(str(path) for path in reroute_additional_files)])
    if validation_manifest.get("ignore_errors", True):
        reroute_command.append("--ignore-errors")
    if validation_manifest.get("repair", True):
        reroute_command.append("--repair")
    if validation_manifest.get("write_trips", True):
        reroute_command.extend(["--write-trips", "true"])
    if validation_manifest.get("ptline_routing", True) and runtime_config.get("pt_vehicles_file"):
        reroute_command.extend(["--ptline-routing", "true"])
    run_command(reroute_command, cwd=reroute_dir, dry_run=dry_run, quiet=True)
    return final_route_path


def write_sumocfg(sumocfg_path: Path, net_file: Path, route_files: list[Path], additional_files: list[Path],
                  sumo_manifest: dict) -> None:
    log_info(f"[PIPELINE] Writing SUMO configuration to {sumocfg_path}\n")
    root = ET.Element(
        "sumoConfiguration",
        {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/sumoConfiguration.xsd",
        },
    )

    input_el = ET.SubElement(root, "input")
    ET.SubElement(input_el, "net-file", {"value": str(net_file)})
    ET.SubElement(input_el, "route-files", {"value": ",".join(str(path) for path in route_files)})
    if additional_files:
        ET.SubElement(input_el, "additional-files", {"value": ",".join(str(path) for path in additional_files)})

    outputs = sumo_manifest.get("outputs", {})
    output_el = ET.SubElement(root, "output")
    if outputs.get("summary"):
        ET.SubElement(output_el, "summary-output", {"value": outputs["summary"]})
    if outputs.get("statistic"):
        ET.SubElement(output_el, "statistic-output", {"value": outputs["statistic"]})
    if outputs.get("tripinfo"):
        ET.SubElement(output_el, "tripinfo-output", {"value": outputs["tripinfo"]})
    if outputs.get("personinfo"):
        ET.SubElement(output_el, "personinfo-output", {"value": outputs["personinfo"]})
    if outputs.get("edge_data"):
        ET.SubElement(output_el, "edgedata-output", {"value": outputs["edge_data"]})
    if outputs.get("collision"):
        ET.SubElement(output_el, "collision-output", {"value": outputs["collision"]})

    time_el = ET.SubElement(root, "time")
    ET.SubElement(time_el, "begin", {"value": str(sumo_manifest.get("begin", 0))})
    ET.SubElement(time_el, "end", {"value": str(sumo_manifest.get("end", 86400))})
    ET.SubElement(time_el, "step-length", {"value": str(sumo_manifest.get("step_length", 1))})

    processing_el = ET.SubElement(root, "processing")
    ET.SubElement(processing_el, "route-steps", {"value": "200"})
    ET.SubElement(processing_el, "time-to-teleport", {"value": "20"})
    ET.SubElement(processing_el, "ignore-route-errors", {"value": "true"})

    device_el = ET.SubElement(root, "device")
    ET.SubElement(device_el, "device.taxi.dispatch-algorithm", {"value": "greedy"})
    ET.SubElement(device_el, "device.taxi.dispatch-period", {"value": "30"})
    ET.SubElement(device_el, "device.taxi.idle-algorithm", {"value": "stop"})

    report_el = ET.SubElement(root, "report")
    if outputs.get("log"):
        ET.SubElement(report_el, "log", {"value": outputs["log"]})
    ET.SubElement(report_el, "verbose", {"value": "true"})
    ET.SubElement(report_el, "no-step-log", {"value": "false"})
    ET.SubElement(report_el, "no-warnings", {"value": "true"})

    ET.ElementTree(root).write(sumocfg_path, encoding="utf-8", xml_declaration=True)


def resolve_sumo_outputs(manifest: dict, day_sumo_dir: Path, dry_run: bool) -> dict[str, Path]:
    outputs = manifest["sumo"]["outputs"]
    engine = manifest["sumo"].get("engine", "sumo")
    if engine == "sumo":
        return {
            "tripinfo": day_sumo_dir / outputs["tripinfo"],
            "personinfo": day_sumo_dir / outputs["personinfo"],
        }
    results_dir = day_sumo_dir / outputs.get("results_dir", "uam_results")
    if dry_run:
        return {
            "tripinfo": results_dir / outputs["tripinfo"],
            "personinfo": results_dir / outputs["personinfo"],
        }
    direct_tripinfo = day_sumo_dir / outputs["tripinfo"]
    direct_personinfo = day_sumo_dir / outputs["personinfo"]
    if direct_tripinfo.exists() and direct_personinfo.exists():
        return {
            "tripinfo": direct_tripinfo,
            "personinfo": direct_personinfo,
        }
    tripinfo_candidates = sorted(results_dir.rglob(outputs["tripinfo"]), key=lambda path: path.stat().st_mtime)
    personinfo_candidates = sorted(results_dir.rglob(outputs["personinfo"]), key=lambda path: path.stat().st_mtime)
    if not tripinfo_candidates or not personinfo_candidates:
        raise FileNotFoundError(f"Could not locate SUMO outputs under {results_dir}")
    return {
        "tripinfo": tripinfo_candidates[-1],
        "personinfo": personinfo_candidates[-1],
    }


def run_final_sumo(sumocfg_path: Path, manifest: dict, day_sumo_dir: Path, dry_run: bool) -> None:
    log_info(f"[PIPELINE] Running final SUMO simulation.\n")
    sumo_manifest = manifest["sumo"]
    engine = sumo_manifest.get("engine", "sumo")

    if engine == "sumo":
        command = [resolve_binary("sumo"), "-c", str(sumocfg_path)]
        run_command(command, cwd=day_sumo_dir, dry_run=dry_run)
        return

    if engine == "uam_traci":
        results_dir_name = sumo_manifest.get("outputs", {}).get("results_dir", "uam_results")
        results_dir = ensure_dir(day_sumo_dir / results_dir_name)
        command = [
            sys.executable,
            str(REPO_ROOT / "uam-sumo" / "uamTraCI.py"),
            "--scenario_path", str(sumocfg_path),
            "--results-dir", str(results_dir),
        ]
        if sumo_manifest.get("nogui", True):
            command.append("--nogui")
        if "uam_step_size" in sumo_manifest:
            command.extend(["--uam_step_size", str(sumo_manifest["uam_step_size"])])
        if "uam_start_density" in sumo_manifest:
            command.extend(["--uam_start_density", str(sumo_manifest["uam_start_density"])])
        if "uam_upper_bound" in sumo_manifest:
            command.extend(["--uam_upper_bound", str(sumo_manifest["uam_upper_bound"])])
        run_command(command, cwd=REPO_ROOT / "uam-sumo", dry_run=dry_run)
        return

    raise ValueError(f"Unsupported SUMO engine {engine!r}")


def run_postprocess_day(
    manifest: dict,
    runtime_config: dict,
    static_route_files: list[Path],
    output_root: Path,
    day_index: int,
    dry_run: bool,
) -> tuple[Path, Path]:
    day_trip_path = Path(runtime_config["storage_path"]) / f"day{day_index}" / "trips.xml"
    day_uam_trip_path = Path(runtime_config["storage_path"]) / f"day{day_index}" / "uam_trips.xml"
    day_post_dir = ensure_dir(output_root / "post" / f"day{day_index}")
    day_sumo_dir = ensure_dir(output_root / "sumo" / f"day{day_index}")
    agents_path = Path(runtime_config["storage_path"]) / f"day{day_index}" / f"agents_{ROUTES_POSTFIX}.json"
    has_uam_trip_file = routes_file_has_entries(day_uam_trip_path)

    taxi_fleet_path = None
    if runtime_config.get("uam_hubs_file"):
        taxi_fleet_path = build_taxi_fleet_if_needed(runtime_config, day_post_dir, dry_run=dry_run)

    validated_trips_path = run_validation(
        day_trip_path=day_trip_path,
        runtime_config=runtime_config,
        static_route_files=static_route_files,
        day_post_dir=day_post_dir,
        validation_manifest=manifest["validation"],
        dry_run=dry_run,
    )
    validated_trips_path = run_population_scaling(
        validated_trips_path=validated_trips_path,
        runtime_config=runtime_config,
        agents_path=agents_path,
        dry_run=dry_run,
        trip_kind="ground",
    )
    final_uam_route_path = None
    if has_uam_trip_file:
        validated_uam_trips_path = run_validation(
            day_trip_path=day_uam_trip_path,
            runtime_config=runtime_config,
            static_route_files=static_route_files,
            day_post_dir=day_post_dir,
            validation_manifest=manifest["validation"],
            dry_run=dry_run,
            output_name="validated_uam.trips.xml",
        )
        final_uam_route_path = run_population_scaling(
            validated_trips_path=validated_uam_trips_path,
            runtime_config=runtime_config,
            agents_path=agents_path,
            dry_run=dry_run,
            trip_kind="uam",
        )
    dua_route_path, dua_weight_path = calculate_dua_weights(
        validated_trips_path=validated_trips_path,
        runtime_config=runtime_config,
        taxi_fleet_path=taxi_fleet_path,
        day_post_dir=day_post_dir,
        dua_manifest=manifest["dua"],
        dry_run=dry_run,
    )
    final_route_path = apply_dua_weight(
        validated_trips_path=validated_trips_path,
        dua_weight_path=dua_weight_path,
        runtime_config=runtime_config,
        day_post_dir=day_post_dir,
        validation_manifest=manifest["validation"],
        dry_run=dry_run,
    )

    sumocfg_path = day_sumo_dir / f"{manifest['name']}_day{day_index}.sumocfg"
    route_files = unique_paths(
        *static_route_files,
        taxi_fleet_path,
        final_route_path,
        final_uam_route_path,
    )
    additional_files = unique_paths(runtime_config.get("v_types_file"), runtime_config.get("pt_stops_file"))
    if dry_run:
        log_info(f"[PIPELINE] Would write {sumocfg_path}")
    else:
        write_sumocfg(
            sumocfg_path=sumocfg_path,
            net_file=Path(runtime_config["net_file"]),
            route_files=route_files,
            additional_files=additional_files,
            sumo_manifest=manifest["sumo"],
        )
    run_final_sumo(sumocfg_path, manifest, day_sumo_dir, dry_run=dry_run)

    sumo_outputs = resolve_sumo_outputs(manifest, day_sumo_dir, dry_run=dry_run)
    if dry_run:
        log_info(
            f"[PIPELINE] Would return SUMO output paths for journey outcome extraction: "
            f"{sumo_outputs['tripinfo']} and {sumo_outputs['personinfo']}"
        )
    return sumo_outputs["tripinfo"], sumo_outputs["personinfo"]


def run_batch_scenario(
    manifest: dict,
    dry_run: bool,
    resume_day: int | None = None,
    resume_stage: str = "llm",
) -> None:
    output_root = ensure_dir(manifest["output_root"])
    log_path = configure_pipeline_logging(output_root, manifest["name"])
    preloaded_agents_path = resolve_preloaded_agents_path(manifest)
    uam_artifacts = maybe_build_uam(manifest, dry_run=dry_run)
    runtime_config, context = build_runtime_config(manifest, uam_artifacts)
    ensure_dir(Path(runtime_config["storage_path"]))
    (output_root / "run_metadata.json").write_text(
        json.dumps(
            {
                "scenario": manifest["name"],
                "mode": manifest["mode"],
                "sim_config_name": manifest["sim_config_name"],
                "day_config_path": str(manifest["day_config_path"]),
                "output_root": str(output_root),
                "log_filename": str(log_path),
                "preloaded_agents_path": preloaded_agents_path,
                "runtime_config": runtime_config,
                "uam_artifacts": uam_artifacts,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    validated_day_config = load_day_config(manifest["day_config_path"], runtime_config)
    start_day = resume_day or 1
    if start_day < 1 or start_day > validated_day_config.num_days:
        raise ValueError(f"resume_day must be between 1 and {validated_day_config.num_days}, got {start_day}")

    if not dry_run:
        if resume_day is None:
            run_multi_day(
                config=runtime_config,
                day_config_path=manifest["day_config_path"],
                initial_preloaded_agents_path=preloaded_agents_path,
            )
        elif resume_stage == "postprocess":
            log_info(f"[PIPELINE] Resuming batch scenario postprocess from day {start_day}.")
        else:
            state = prepare_simulation(
                config=runtime_config,
                day_config_path=manifest["day_config_path"],
                initial_preloaded_agents_path=preloaded_agents_path,
                preserve_existing_days=True,
            )
            continue_multi_day_from_state(
                state,
                start_day=start_day,
                start_stage=resume_stage,
            )

    for day_index in range(start_day, validated_day_config.num_days + 1):
        run_postprocess_day(
            manifest=manifest,
            runtime_config=runtime_config,
            static_route_files=context["static_route_files"],
            output_root=output_root,
            day_index=day_index,
            dry_run=dry_run,
        )


def run_per_day_scenario(
    manifest: dict,
    dry_run: bool,
    resume_day: int | None = None,
    resume_stage: str = "llm",
) -> None:
    output_root = ensure_dir(manifest["output_root"])
    log_path = configure_pipeline_logging(output_root, manifest["name"])
    preloaded_agents_path = resolve_preloaded_agents_path(manifest)
    uam_artifacts = maybe_build_uam(manifest, dry_run=dry_run)
    runtime_config, context = build_runtime_config(manifest, uam_artifacts)
    state = prepare_simulation(
        config=runtime_config,
        day_config_path=manifest["day_config_path"],
        initial_preloaded_agents_path=preloaded_agents_path,
        preserve_existing_days=resume_day is not None,
    )
    (output_root / "run_metadata.json").write_text(
        json.dumps(
            {
                "scenario": manifest["name"],
                "mode": manifest["mode"],
                "sim_config_name": manifest["sim_config_name"],
                "day_config_path": str(manifest["day_config_path"]),
                "output_root": str(output_root),
                "log_filename": str(log_path),
                "preloaded_agents_path": preloaded_agents_path,
                "runtime_config": runtime_config,
                "uam_artifacts": uam_artifacts,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    start_day = resume_day or 1
    if start_day < 1 or start_day > len(state["day_labels"]):
        raise ValueError(f"resume_day must be between 1 and {len(state['day_labels'])}, got {start_day}")

    for day_index in range(start_day, len(state["day_labels"]) + 1):
        if day_index == start_day and resume_day is not None:
            if dry_run:
                log_info(f"[PIPELINE] Would resume day {day_index} from stage {resume_stage!r}.")
                final_agents = []
            else:
                final_agents = resume_day(state, day_index, stage=resume_stage)
        else:
            final_agents = [] if dry_run else run_day_from_state(state, day_index)

        tripinfo_path, personinfo_path = run_postprocess_day(
            manifest=manifest,
            runtime_config=runtime_config,
            static_route_files=context["static_route_files"],
            output_root=output_root,
            day_index=day_index,
            dry_run=dry_run,
        )
        if not dry_run:
            prepare_next_day(
                state,
                day_index,
                tripinfo_path=tripinfo_path,
                personinfo_path=personinfo_path,
            )


def main() -> None:
    args = parse_args()
    if not args.scenario and not args.scenarios and not args.all:
        raise SystemExit("Pass --scenario <name>, --scenarios <name...>, or --all.")

    if args.all:
        selected = sorted(PIPELINE_SCENARIOS)
    elif args.scenarios:
        selected = args.scenarios
    else:
        selected = [args.scenario]
    for scenario_name in selected:
        manifest = load_manifest(scenario_name)
        mode = manifest.get("mode", "batch")
        if mode == "batch":
            run_batch_scenario(
                manifest,
                dry_run=args.dry_run,
                resume_day=args.resume_day,
                resume_stage=args.resume_stage,
            )
        elif mode in {"per_day", "incremental"}:
            run_per_day_scenario(
                manifest,
                dry_run=args.dry_run,
                resume_day=args.resume_day,
                resume_stage=args.resume_stage,
            )
        else:
            raise NotImplementedError(f"Unsupported scenario mode {mode!r}")
        log_info(f"[PIPELINE] Completed scenario {scenario_name}.\n")

if __name__ == "__main__":
    main()
