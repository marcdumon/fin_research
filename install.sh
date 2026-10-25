#!/usr/bin/env bash
# Install or update fin_research as an app, apart from this repo: one version from GitHub in a Python environment in the app
# folder, and the settings files on the first install. An update replaces only the environment.
# Usage: ./install.sh <version>                                for example ./install.sh v0.1.0
#        FIN_RESEARCH_HOME=/other/app/folder ./install.sh <version>
set -euo pipefail

VERSION=${1:?Give the version to install, for example: ./install.sh v0.1.0}
APP=${FIN_RESEARCH_HOME:-$HOME/.local/share/md_apps/fin_research}
REPO=marcdumon/fin_research

for tool in uv npm curl; do
    command -v "$tool" >/dev/null || { echo "Missing '$tool': install it first." >&2; exit 1; }
done

mkdir -p "$APP"
[[ -d $APP/env ]] || uv venv --quiet --python 3.13 "$APP/env"
echo "Installing fin_research $VERSION in $APP/env ..."
uv pip install --quiet --python "$APP/env/bin/python" --reinstall-package fin-research "git+https://github.com/$REPO@$VERSION"

for name in fin_research.example screens; do
    target=$APP/${name%.example}.toml
    if [[ ! -f $target ]]; then
        curl -sfL "https://raw.githubusercontent.com/$REPO/$VERSION/$name.toml" -o "$target"
        echo "Made $target: set your own values in it."
    fi
done
echo "Installed. Start the app with: $APP/env/bin/fin-research"
