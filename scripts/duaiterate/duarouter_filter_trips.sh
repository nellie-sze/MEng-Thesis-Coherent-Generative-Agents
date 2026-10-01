#!/bin/bash
#SBATCH --job-name=DUAROUTER_FILTER
#SBATCH --partition=paula
#SBATCH --time=48:00:00
#SBATCH --mem-per-cpu=100GB
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
  --route-files PATHS       Additional route files (comma-separated)
  --additional-files PATHS  Additional files (comma-separated)
  --output PATH             Output validated trips XML path
  --workdir DIR             Working directory for logs/output
  -h, --help                Show this help
EOF
}

NET_FILE=""
TRIPS_FILE=""
ROUTE_FILES=""
ADDITIONAL_FILES=""
WORKDIR="duarouter_filter_trips"
OUTPUT_FILE="validated.trips.xml"

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
    --output)
      OUTPUT_FILE="$2"
      shift 2
      ;;
    --workdir)
      WORKDIR="$2"
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

mkdir -p "$WORKDIR"
cd "$WORKDIR"

OUTPUT_BASENAME="$(basename "$OUTPUT_FILE")"
TRIP_INPUTS="$TRIPS_FILE"
if [[ -n "$ROUTE_FILES" ]]; then
  TRIP_INPUTS="${TRIP_INPUTS},${ROUTE_FILES}"
fi

# Filters only normal trips
duarouter \
  -n "$NET_FILE" \
  -r "$TRIP_INPUTS" \
  --additional-files "$ADDITIONAL_FILES" \
  --routing-threads 8 \
  --ignore-errors \
  --write-trips true \
  -o "$OUTPUT_BASENAME" \
  --log duarouter_filter.log
