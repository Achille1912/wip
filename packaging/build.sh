#!/usr/bin/env bash
# Builds the installable app with its own Python inside, so the target machine needs only git and a Claude login.
#   ./packaging/build.sh windows   (Git Bash on Windows)  -> dist/Blueprint-windows-x64.zip   portable, unzip and double-click Blueprint.cmd
#   ./packaging/build.sh linux     (Linux or WSL)         -> dist/Blueprint-x86_64.AppImage
# Only git-tracked files are packed: work/ (blueprints of client code) never ends up in the app.
set -euo pipefail
cd "$(dirname "$0")/.."
PBS=20261026 PYV=3.12.16 SDK=claude-agent-sdk==0.2.168 AIT=1.9.1  # SDK: keep in step with pyproject.toml
case "${1:-}" in
  windows) T=x86_64-pc-windows-msvc   D=dist/Blueprint          PY=python/python.exe ;;
  linux)   T=x86_64-unknown-linux-gnu D=dist/Blueprint.AppDir PY=python/bin/python3 ;;
  *) echo "usage: $0 windows|linux" >&2; exit 2 ;;
esac
get() { [ -f "dist/cache/$1" ] || curl -fL --create-dirs -o "dist/cache/$1" "$2"; }

rm -rf "$D" && mkdir -p "$D/app"
TGZ=cpython-$PYV+$PBS-$T-install_only.tar.gz
get "$TGZ" "https://github.com/astral-sh/python-build-standalone/releases/download/$PBS/${TGZ/+/%2B}"
tar -xzf "dist/cache/$TGZ" -C "$D"  # -> $D/python
"$D/$PY" -m pip install -q --disable-pip-version-check --no-warn-script-location "$SDK"
git -c safe.directory='*' ls-files -z blueprint-proto viewer-kit | tar --null -T - -cf - | tar -xf - -C "$D/app"

if [ "$1" = windows ]; then
  sed 's/$/\r/' > "$D/Blueprint.cmd" <<'EOF'
@echo off
rem portable: blueprints, notes and runs stay in data\ next to the app
set "BLUEPRINT_DATA=%~dp0data"
if "%~1"=="" ("%~dp0python\python.exe" "%~dp0app\blueprint-proto\blueprint_cli.py" --pick) else ("%~dp0python\python.exe" "%~dp0app\blueprint-proto\blueprint_cli.py" %*)
if errorlevel 1 pause
EOF
  rm -f dist/Blueprint-windows-x64.zip
  "$D/$PY" -m zipfile -c dist/Blueprint-windows-x64.zip "$D"
  echo "built dist/Blueprint-windows-x64.zip"
else
  cat > "$D/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export BLUEPRINT_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/blueprint"  # the AppImage itself is read-only
[ $# -eq 0 ] && set -- --pick
exec "$HERE/python/bin/python3" "$HERE/app/blueprint-proto/blueprint_cli.py" "$@"
EOF
  chmod +x "$D/AppRun"
  printf '[Desktop Entry]\nType=Application\nName=Blueprint\nExec=blueprint\nIcon=blueprint\nCategories=Development;\n' > "$D/blueprint.desktop"
  cp packaging/blueprint.svg "$D/blueprint.svg" && cp packaging/blueprint.svg "$D/.DirIcon"
  get appimagetool-$AIT "https://github.com/AppImage/appimagetool/releases/download/$AIT/appimagetool-x86_64.AppImage"
  chmod +x "dist/cache/appimagetool-$AIT"
  ARCH=x86_64 "dist/cache/appimagetool-$AIT" --appimage-extract-and-run "$D" dist/Blueprint-x86_64.AppImage
fi
