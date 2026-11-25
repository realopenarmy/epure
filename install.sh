#!/usr/bin/env bash
# Épure installer - idempotent. Resolves its own directory, so it works
# wherever the repo is cloned. The usual layout is:
#   bash /workspace/epure/install.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/venv"
PY="${PYTHON:-python3}"   # needs Python 3.10-3.13 (cadquery-ocp wheels)

# 1. system bits (fonts for text rendering, GL libs some OCP builds load, poppler
# for checking PDFs, and the stdlib venv module — Ubuntu does not ship it with python3)
need_apt=()
py_mm="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
for pkg in fonts-dejavu-core fonts-liberation libgl1 libglu1-mesa libxrender1 poppler-utils python3-venv "python${py_mm}-venv"; do
  dpkg -s "$pkg" >/dev/null 2>&1 || need_apt+=("$pkg")
done
if [ ${#need_apt[@]} -gt 0 ] && command -v apt-get >/dev/null; then
  SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null && SUDO="sudo"
  export DEBIAN_FRONTEND=noninteractive
  $SUDO apt-get update -qq && $SUDO apt-get install -y -qq "${need_apt[@]}" || echo "warn: apt install failed for ${need_apt[*]} (continuing)"
fi

# 2. python venv with pinned versions
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
if ! "$VENV/bin/python" -c "import cadquery, OCP, ezdxf, matplotlib, trimesh, pypdf" 2>/dev/null; then
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q \
    cadquery==2.8.0 cadquery-ocp==7.9.3.1.1 ezdxf==1.4.5 matplotlib==3.11.2 numpy==2.5.5 pillow==13.0.0 trimesh==5.2.2 pypdf==7.1.1
fi

# 3. wrapper + smoke test. The wrapper finds its own directory (same text as the
# committed ./epure), so re-running install does not pin a machine path.
cat > "$HERE/epure" <<'WRAP'
#!/usr/bin/env bash
# Resolves the directory this wrapper lives in, so a clone works at
# /workspace/epure or anywhere else.
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$HERE/venv/bin/python" "$HERE/epure.py" "$@"
WRAP
chmod +x "$HERE/epure"
"$VENV/bin/python" -c "import cadquery, OCP, ezdxf; print('epure ok: cadquery', cadquery.__version__, '| ezdxf', ezdxf.__version__)"
