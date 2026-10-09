#!/bin/sh
set -eu

plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
data_dir="${CODEX_HOME:-$HOME/.codex}/model-recommender"
export UV_CACHE_DIR="$data_dir/cache"
export UV_PYTHON_INSTALL_DIR="$data_dir/python"
export HF_HOME="$data_dir/models"
python="$data_dir/venv/bin/python"

case "${1:-hook}" in
    setup)
        mkdir -p "$data_dir"
        if [ ! -x "$data_dir/bin/uv" ]; then
            printf '%s\n' 'Downloading the setup tool…'
            installer=$(mktemp)
            trap 'rm -f "$installer"' EXIT HUP INT TERM
            curl --fail --location --silent --show-error https://astral.sh/uv/install.sh -o "$installer"
            UV_UNMANAGED_INSTALL="$data_dir/bin" sh "$installer"
        fi
        rm -f "$data_dir/ready"
        if [ ! -x "$python" ]; then
            printf '%s\n' 'Preparing private Python…'
            "$data_dir/bin/uv" --no-config venv --managed-python --python 3.12 "$data_dir/venv"
        fi
        printf '%s\n' 'Installing the classifier…'
        "$data_dir/bin/uv" --no-config pip install --python "$python" 'laya==0.4.0'
        printf '%s\n' 'Downloading the model (the first download can take a few minutes)…'
        HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 "$python" -c "from laya import Router; Router().preload(['english'])"
        touch "$data_dir/ready"
        printf '%s\n' 'Model Recommender setup complete. Trust the hook in Codex, then start a new chat.'
        ;;
    hook)
        if [ -f "$data_dir/ready" ] && [ -x "$python" ]; then
            exec "$python" -B "$plugin_root/hook.py"
        fi
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Briefly tell the user to run Model Recommender setup from the plugin settings, or ask Codex to set up Model Recommender. Continue the current task using the current settings."}}'
        ;;
    stop)
        if [ -f "$data_dir/ready" ] && [ -x "$python" ]; then
            exec "$python" -B "$plugin_root/hook.py"
        fi
        printf '%s\n' '{}'
        ;;
    *)
        printf '%s\n' 'Usage: sh runtime.sh [setup|hook|stop]' >&2
        exit 2
        ;;
esac
