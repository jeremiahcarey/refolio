#!/bin/bash
# Double-click to start Earmark. The first run sets things up, which takes a minute or two.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "First-time setup: installing uv, which installs Python and Earmark's parts…"
  if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
    echo "Couldn't install uv. Check your internet connection, then double-click Earmark again."
    read -r -n 1 -p "Press any key to close."
    exit 1
  fi
  export PATH="$HOME/.local/bin:$PATH"
fi
exec uv run --python 3.12 --project . earmark "$@"
