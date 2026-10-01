#!/bin/bash
#SBATCH --job-name=DUAITERATE
#SBATCH --partition=paul-long
#SBATCH --time=240:00:00
#SBATCH --mem-per-cpu=20GB
#SBATCH --nodes=1
#SBATCH --cpus-per-task=8
#SBATCH --ntasks-per-node=1

#module load SUMO/1.22.0-foss-2023a

usage() {
  cat << EOF
Usage: $0 [options]

Options:
  --net-file PATH           SUMO network file
  --trips-file PATH         Main trips XML file
  --route-files PATHS       Additional route inputs for DUA (comma-separated)
  --additional-files PATHS  Additional files for SUMO/duarouter (comma-separated)
  --output DIR              Output directory for DUA results
  --workdir DIR             Alias for --output
  -h, --help                Show this help
EOF
}

if [ -d "$HOME/sumo" ]; then
    echo "Repository exists. Updating..."
    cd "$HOME/sumo"
    git pull
    git submodule update --init --recursive
    cd -
else
    echo "Repository not found. Cloning..."
    git clone --recursive https://github.com/eclipse-sumo/sumo.git "$HOME/sumo"
fi

NET_FILE=""
TRIPS_FILE=""
ROUTE_FILES=""
ADDITIONAL_FILES=""
OUTPUT_DIR="results_duaiterate"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --net-file)
      NET_FILE="$2"
      shift 2
      ;;
    --trips-file)
      TRIPS_FILE="$2"
      shift 2
      ;;
    --route-files)
      ROUTE_FILES="$2"
      shift 2
      ;;
    --additional-files)
      ADDITIONAL_FILES="$2"
      shift 2
      ;;
    --output|--workdir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$NET_FILE" || -z "$TRIPS_FILE" || -z "$ADDITIONAL_FILES" ]]; then
  echo "Error: --net-file, --trips-file, and --additional-files are required." >&2
  usage
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
cd "$OUTPUT_DIR"

TRIP_INPUTS="$TRIPS_FILE"
if [[ -n "$ROUTE_FILES" ]]; then
  TRIP_INPUTS="${TRIP_INPUTS},${ROUTE_FILES}"
fi

python3 ~/sumo/tools/assign/duaIterate.py \
    -n "${NET_FILE}" \
    -t "${TRIP_INPUTS}" \
    --additional "${ADDITIONAL_FILES}" \
    --duarouter-additional-files "${ADDITIONAL_FILES}" \
    --duarouter-routing-threads 8 \
    --continue-on-unbuild \
    --sumo-ignore-route-errors "true" \
    --time-inc 8640 \
    --weight-memory \
    --pessimism 1 \
    --router-verbose \
    --inc-start 0.033 \
    --inc-base 30 \
    --inc-max 1 \
    --incrementation 1 \
    --time-to-teleport 20 \
    --sumo--personinfo-output "../personinfo.xml" \
    --log "duaiterate.log"
