#!/bin/sh
set -eu

plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
data_dir="${CODEX_HOME:-$HOME/.codex}/model-recommender"
plugin_data_dir="$data_dir/plugin"
export UV_CACHE_DIR="$data_dir/cache"
export UV_PYTHON_INSTALL_DIR="$data_dir/python"
export HF_HOME="$data_dir/models"
python="$data_dir/venv/bin/python"
lock_dir="$data_dir/setup.lock"

fingerprint() {
    {
        cat "$plugin_root/.codex-plugin/plugin.json"
        cksum "$plugin_root/runtime.sh" "$plugin_root/mcp_server.py" "$plugin_root/hook.py" \
            "$plugin_root/router.json" "$plugin_root/questions.json"
    } | cksum | awk '{ print $1 "-" $2 }'
}

is_ready() {
    [ -x "$data_dir/bin/uv" ] && [ -x "$python" ] && [ -f "$data_dir/ready" ] &&
        [ "$(cat "$data_dir/ready")" = "$(fingerprint)" ] &&
        [ -f "$plugin_data_dir/mcp_server.py" ] && [ -f "$plugin_data_dir/hook.py" ]
}

setup() {
    mkdir -p "$data_dir"
    if command -v lockf >/dev/null 2>&1; then
        exec lockf -k "$lock_dir" sh "$plugin_root/runtime.sh" setup-locked
    elif command -v flock >/dev/null 2>&1; then
        exec flock -x "$lock_dir" sh "$plugin_root/runtime.sh" setup-locked
    fi
    printf '%s\n' 'Model Recommender setup needs lockf (macOS) or flock (Linux) to serialize installation.' >&2
    return 1
}

setup_locked() {
    if is_ready; then
        return
    fi
    if [ ! -x "$data_dir/bin/uv" ]; then
        printf '%s\n' 'Downloading the setup tool…'
        installer=$(mktemp)
        trap 'rm -f "$installer"' EXIT HUP INT TERM
        curl --fail --location --silent --show-error https://astral.sh/uv/install.sh -o "$installer"
        UV_UNMANAGED_INSTALL="$data_dir/bin" sh "$installer"
        rm -f "$installer"
        trap - EXIT HUP INT TERM
    fi
    rm -f "$data_dir/ready"
    if [ ! -x "$python" ]; then
        printf '%s\n' 'Preparing private Python…'
        "$data_dir/bin/uv" --no-config venv --managed-python --python 3.12 "$data_dir/venv"
    fi
    printf '%s\n' 'Installing the classifier…'
    # MCP 1.12.4 imports a Pydantic private helper removed in 2.14.
    "$data_dir/bin/uv" --no-config pip install --python "$python" 'laya==0.4.0' 'mcp==1.12.4' 'pydantic<2.14'
    printf '%s\n' 'Downloading the model (the first download can take a few minutes)…'
    HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 "$python" -c "from laya import Router; Router().preload(['english'])"
    mkdir -p "$plugin_data_dir"
    cp "$plugin_root/runtime.sh" "$plugin_root/mcp_server.py" "$plugin_root/hook.py" \
        "$plugin_root/router.json" "$plugin_root/questions.json" "$plugin_data_dir/"
    fingerprint > "$data_dir/ready.tmp"
    mv "$data_dir/ready.tmp" "$data_dir/ready"
    printf '%s\n' 'Model Recommender setup complete. Trust the hook in Codex, then start a new chat.'
}

case "${1:-mcp}" in
    setup)
        setup
        ;;
    setup-locked)
        setup_locked
        ;;
    mcp)
        if ! is_ready; then
            sh "$plugin_root/runtime.sh" setup >&2 || { setup_status=$?; exit "$setup_status"; }
        fi
        exec "$python" -B "$plugin_root/mcp_server.py"
        ;;
    *)
        printf '%s\n' 'Usage: sh runtime.sh [setup|mcp]' >&2
        exit 2
        ;;
esac
