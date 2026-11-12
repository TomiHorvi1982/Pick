#!/bin/sh
# Builds the engine for this Mac and for Windows (WASAPI) into engine/dist/, where Pick's jam gateway
# offers it to the friend (/engine/<name>). Windows needs: brew install mingw-w64;
# rustup target add x86_64-pc-windows-gnu. The ASIO build comes from GitHub Actions (.github/workflows/engine.yml).
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/opt/rustup/bin:$PATH"
cargo build --release
cargo build --release --target x86_64-pc-windows-gnu
mkdir -p dist
cp target/release/pick-jam-engine dist/pick-jam-engine-mac
cp target/x86_64-pc-windows-gnu/release/pick-jam-engine.exe dist/pick-jam-engine.exe
# the ASIO build (WASAPI + ASIO) from the last GitHub Actions run replaces the WASAPI-only one, when there is one
if command -v gh >/dev/null && rm -rf dist/asio && gh run download -n pick-jam-engine-windows-asio -D dist/asio 2>/dev/null; then
  cp dist/asio/pick-jam-engine.exe dist/pick-jam-engine.exe && echo "Windows: ASIO build from GitHub Actions"
else
  echo "Windows: WASAPI build from this Mac (no ASIO build on GitHub yet)"
fi
rm -rf dist/asio
ls -la dist
