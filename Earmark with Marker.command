#!/bin/bash
# Like Earmark.command, but also installs Marker, the slower, more accurate converter.
# The first run downloads PyTorch and Marker (a few GB), so it takes a while.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "First-time setup: installing uv, which installs Python and Earmark's parts…"
  if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
    echo "Couldn't install uv. Check your internet connection, then double-click again."
    read -r -n 1 -p "Press any key to close."
    exit 1
  fi
  export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v llama-server >/dev/null 2>&1; then
  echo "Note: Marker reads scanned pages with llama.cpp, which isn't installed."
  echo "Born-digital PDFs work without it. For scans, install Homebrew (https://brew.sh), then run:"
  echo "    brew install llama.cpp"
  echo
fi
exec uv run --python 3.12 --project . --extra marker earmark "$@"
