#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -r requirements.txt

export TM_BTC_ADDRESS="${TM_BTC_ADDRESS:-bc1qexampleaddressxxxxxxxxxxxxxxxxxxxxxx}"
export TM_ORIGIN_LABEL="${TM_ORIGIN_LABEL:-Vancouver Island, BC, Canada}"

python app.py
