#!/usr/bin/env bash
set -euo pipefail
umask 077

phase="${1:-}"
export CARGO_HOME=/cache/cargo
export CARGO_TARGET_DIR=/out/target
export HOME=/tmp/home
mkdir -p "$HOME" "$CARGO_HOME" "$CARGO_TARGET_DIR"

case "$phase" in
  fetch)
    cargo fetch --locked --manifest-path ./Cargo.toml
    ;;
  build)
    cargo build --release --locked --offline --target wasm32-unknown-unknown --manifest-path ./Cargo.toml
    mkdir -p /out/artifacts
    find "$CARGO_TARGET_DIR/wasm32-unknown-unknown/release" -maxdepth 1 -type f -name '*.wasm' -exec cp -f '{}' /out/artifacts/ ';'
    ;;
  *)
    echo "usage: tine-plugin-build fetch|build" >&2
    exit 2
    ;;
esac
