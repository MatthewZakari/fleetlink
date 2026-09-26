#!/usr/bin/env bash
set -euo pipefail

echo "Setting up FleetLink development environment..."

# Install system development dependencies required by FleetLink.
sudo apt-get update
sudo apt-get install -y --no-install-recommends git-lfs


# Install uv at the version currently used by FleetLink.
curl -LsSf https://astral.sh/uv/0.12.19/install.sh | sh

# Install Flutter stable if it is not already available.
if ! command -v flutter >/dev/null 2>&1; then
    git clone \
        --depth 1 \
        --branch stable \
        https://github.com/flutter/flutter.git \
        "$HOME/flutter"
fi

# Make Flutter available to future shells.
if ! grep -q 'flutter/bin' "$HOME/.bashrc"; then
    echo 'export PATH="$HOME/flutter/bin:$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
fi

export PATH="$HOME/flutter/bin:$HOME/.local/bin:$PATH"

echo
echo "FleetLink tool versions:"
python --version
uv --version
flutter --version
dart --version
docker --version

echo
echo "Installing backend dependencies..."
uv sync --project apps/api --locked

echo
echo "Installing Flutter dependencies..."
(
    cd apps/mobile
    flutter pub get
)

echo
echo "FleetLink development environment ready."
