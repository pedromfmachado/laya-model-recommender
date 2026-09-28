#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CODEX_DIR=${CODEX_HOME:-"$HOME/.codex"}
mkdir -p "$CODEX_DIR"

# Install only Laya-specific files; leave Codex's default config and catalog intact.
cp "$SCRIPT_DIR/config/codex/laya.models.json" "$CODEX_DIR/laya.models.json"
sed "s|@CODEX_HOME@|$CODEX_DIR|g" "$SCRIPT_DIR/config/codex/laya.config.toml" > "$CODEX_DIR/laya.config.toml"

printf 'Installed Laya Codex profile. Start Codex with: codex --profile laya\n'
