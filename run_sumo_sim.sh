#!/bin/bash

# change all of this according to the configuration of your cluster
#SBATCH --time=48:00:00
#SBATCH --mem=250G
#SBATCH --ntasks=8
#SBATCH --job-name=traffic-simulacra
#SBATCH --partition=paula
#SBATCH --gres=gpu:a30:8
#SBATCH --cpus-per-task=1


#module load Python/3.11.5-GCCcore-13.2.0
#module load CUDA/12.4.0
#module load SUMO/1.22.0-foss-2023a

if [ ! -d "venv" ]; then
    python -m venv venv
fi

source venv/Scripts/activate
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
BUNDLED_SUMO_HOME="$REPO_ROOT/sumo-1.26.0"

if [ -n "${SUMO_HOME:-}" ]; then
    export PATH="$SUMO_HOME/bin:$PATH"
elif [ -d "$BUNDLED_SUMO_HOME/bin" ]; then
    export SUMO_HOME="$BUNDLED_SUMO_HOME"
    export PATH="$SUMO_HOME/bin:$PATH"
else
    echo "SUMO_HOME is not set and no bundled SUMO installation was found at $BUNDLED_SUMO_HOME."
    exit 1
fi

pip install --upgrade pip
pip install -r requirements.txt

pip install -e .
python src/multi_day_runner.py
