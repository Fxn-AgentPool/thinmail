#!/bin/sh
# Build release artefacts into dist/:
#   dist/thinmail.pyz   one file, needs python3 (>= 3.10) on the host
#   dist/thinmail       one executable with Python inside (PyInstaller), only with --binary
# The executable links against the build machine's glibc: build on an old distro
# (CI uses ubuntu-22.04, glibc 2.35) so it runs on newer ones too.
set -eu
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
rm -rf build/zipapp && mkdir -p build/zipapp dist
cp -r src/thinmail build/zipapp/
find build/zipapp -name __pycache__ -prune -exec rm -rf {} +
"$PY" -m zipapp build/zipapp -m "thinmail.__main__:main" -p "/usr/bin/env python3" -c -o dist/thinmail.pyz
echo "built dist/thinmail.pyz ($(wc -c < dist/thinmail.pyz) bytes)"

if [ "${1:-}" = "--binary" ]; then
    [ -x build/venv/bin/pyinstaller ] || { "$PY" -m venv build/venv && build/venv/bin/pip install -q "pyinstaller==6.*"; }
    build/venv/bin/pyinstaller --onefile --name thinmail --clean --noconfirm --log-level WARN \
        --distpath dist --workpath build/pyi --specpath build \
        --paths src --add-data "$(pwd)/src/thinmail/static:thinmail/static" \
        scripts/entry.py
    echo "built dist/thinmail ($(du -h dist/thinmail | cut -f1))"
fi
