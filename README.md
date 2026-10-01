# Coherent Generative-Agent Mobility Simulation

This repository contains the research tool built for my Master's dissertation titled: Coherent Generative-Agent Mobility Simulation for Policy Change and Emerging Transport Scenarios

It builds on pre-existing code provided by Simon Lämmer, Mark Colley & Patrick Ebel. Some technical details relating to setup in this ReadMe file are also taken from the [original repository](https://github.com/ciao-group/Generative-Traffic-Agents). 

The extension of GTA to support Urban Air Mobility (or air-taxis) was made possible by Mark Colley's library [uam-sumo](https://github.com/M-Colley/uam-sumo). This repository contains a version modified for compatibility. 

My key contributions to the codebase are:
1. A configurable simulation framework for mobility experiments, including a modular vehicle system for defining mode-specific costs and behavioural constraints and a daily settings system for specifying environmental context and location based events.
2. Three configurable prompt-engineering strategies with automatic conversion of behavioural constraints into natural language prompt content.


The workflow proceeds in two steps:
1. **Schedule & mode generation**  
   Generative Traffic Agents (GTAs) use persona descriptions and prompts to produce daily activity schedules with transport modes.
2. **Traffic simulation**  
   The resulting `trips.xml` feeds into dynamic user-equilibrium tools and SUMO for traffic simulation.


## Preprocessing

In order for the simulation to execute, several files are necessary (street network, public transport specification,
etc.). These are not stored in github due to large file
size. [A .zip file can be downloaded here.](https://osf.io/rbtk7/?view_only=c8b78e8a322542d2bcaa41b7a051d735)

**Note:** It contains all files necessary to run but does not include the B1 data set from the MiD 2017 study. Therefore it either should be be requested from official sources or the "Mikrozensus
2023" file should be used. In order to do that, usages of `SeedGeneratorMiD` can be swapped with `SeedGeneratorCensus`.

### Population data

Our approach uses the [B1 data set from the MiD 2017 study](https://mobilithek.info/offers/823147460382572544) that is
available upon request. However, other sources could be used. A simple, exemplary adaption exists for the "Mikrozensus
2023", the respective source is included in the .zip.
In order to use it, usages of `SeedGeneratorMiD` can be swapped with `SeedGeneratorCensus`.

### Network and public transport

We use openly available GTFS data to incorporate public transport and open street map as source for the network.
To generate a network use `scripts/netgen/setup_berlin_maps.sh` or `scripts/netgen/setup_wedding_maps.sh`. Due to
conversion errors inherent in `netconvert`, manual adaptation might be necessary in `netedit`.
To filter invalid public transportation trips use `scripts/duaiterate/duarouter_filter_pt.sh`.

### Geopackage files

To generate the gpk files used for fast access to buildings associated with specific osm attributes, see `scripts/gpk`.

## Execution

To deploy and execute on the cluster run:

```
chmod +x deploy_to_server.sh
./deploy_to_server.sh
```

To run it on your own machine, `run_sumo_sim.sh` can be used as reference. However, this is not recommended as local LLM
inference and a large simulation network have significant computational demands. If necessary, the local LLM inference
can be replaced with API requests by replacing `huggingface_chat_api.py`.

## Main Workflows

### 1. LLM generation only

Use `multi_day_runner` when you only want GTA outputs (`agents_*.json`, `trips.xml`) and want to run validation / DUA / SUMO yourself afterwards.

```
python -m src.multi_day_runner
```

This uses `src/config/daily_config.py` only. After `trips.xml` is written, the manual steps are:
1. validate trips with `duarouter`
2. optionally scale population with `scripts/population_scaling/agents_replicator.py`
3. run DUA with `sumo/tools/assign/duaIterate.py`
4. reroute with learned weights using `duarouter --weight-files ...`
5. write a `.sumocfg` and run `sumo`

`multi_day_runner` itself only does batch GTA generation: it produces all daily LLM outputs first and does not run SUMO between days, so it does not populate `journey_outcomes` for the next day.

### 2. End-to-end pipeline

Use `run_pipeline.py` to run:

`LLM -> validation -> DUA -> SUMO`

Examples:

```
python scenarios/run_pipeline.py --scenario baseline
python scenarios/run_pipeline.py --scenarios baseline UAM_scenario
python scenarios/run_pipeline.py --all
./run_all_scenarios.sh
```

The pipeline reads scenario definitions from `scenarios/pipeline_manifest.py` and writes outputs under `results/<scenario>/`.

The pipeline supports both `batch` and `per_day` modes. In `batch`, all LLM days are generated first and SUMO is only run afterwards for each day. In `per_day`, the flow is `LLM day N -> validation/DUA/SUMO -> journey outcome extraction -> LLM day N+1`, which is what allows `agents_5_journey_outcomes.json` to be created before the next day is generated.

### UAM scenarios

If a pipeline scenario has a `uam` block with `build=True`, the pipeline builds the UAM network for you before GTA starts. Manual UAM building is only needed if you want to:
- inspect or edit the generated UAM files first
- rebuild hubs outside the pipeline
- use the generated UAM network in a custom workflow

Manual build:

```
python scripts/uam/build_uam_scenario.py --sim-config config_wedding_sumo_UAM --hub-coordinates-file scenarios/uam/uam_hub_coords.json --output-dir data/open_street_map/UAM_scenario_pipeline --output-scenario-name UAM_scenario_pipeline
```

UAM hub locations are configured in `scenarios/uam/uam_hub_coords.json` as `[x, y]` pairs. The generated hub metadata is then written to `data/open_street_map/UAM_scenario_pipeline/*_hubs.json` and used by routing / fleet generation.

## Config Guide

Universal config:
- `src/config/vehicle_config.py`: available vehicle types and their rules
- `src/config/sim_config.py`: base network / SUMO / fleet settings reused by scenarios

Scenario-specific config:
- `src/config/daily_config.py` or `scenarios/day_configs/*.py`: number of days, daily context, events, daily vehicle activate/deactivate rules
- `scenarios/pipeline_manifest.py`: which base sim config to use, output folder, DUA settings, SUMO settings, optional UAM build settings
- `scenarios/uam/*.json`: UAM hub coordinates for built UAM scenarios

Difference between the two workflows:
- `multi_day_runner` only cares about the base sim config plus one day config file
- `run_pipeline.py` uses the same base sim config, but adds scenario-level manifest settings for validation, DUA, SUMO, logging, and optional UAM build overrides

## Run Outputs

Per-day GTA outputs are written under `results/<scenario>/gta/dayN/`.

Common CSV files there:
- `agent_reasoning.csv`: raw reasoning / decision traces for agent scheduling and routing choices
- `day_metrics.csv`: per-day summary counts for that GTA run
- `run_metrics.csv`: higher-level runtime / stage metrics for the run
- `inconsistencies.csv`: agents or routes flagged as internally inconsistent during route generation
- `journey_outcomes.csv`: per-leg post-SUMO outcomes such as `timeLoss`, `crowding`, and `delay` when journey extraction is enabled

Related non-CSV outputs in the same folder:
- `agents_1_description.json` to `agents_5_journey_outcomes.json`: agent state after each GTA stage
- `trips.xml`: the SUMO trips generated by the LLM/GTA stage

Pipeline postprocessing outputs go to:
- `results/<scenario>/post/dayN/` for validation, scaling, DUA, and reroute artefacts
- `results/<scenario>/sumo/dayN/` for final SUMO outputs such as `tripinfo.xml`, `personinfo.xml`, `edge_data.xml`, `summary-output.xml`, and `statistic-output.xml`

## Evaluation

For evaluation scripts see `src/eval`. 
