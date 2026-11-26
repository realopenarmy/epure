#!/usr/bin/env bash
# Épure installer - idempotent. Safe to re-run on a fresh box: bash /workspace/epure/install.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/venv"
PY="${PYTHON:-python3}"   # needs Python 3.10-3.13 (cadquery-ocp wheels)

# 1. system bits (fonts for text rendering, GL libs some OCP builds load, poppler for checking PDFs)
need_apt=()
for pkg in fonts-dejavu-core fonts-liberation libgl1 libglu1-mesa libxrender1 poppler-utils; do
  dpkg -s "$pkg" >/dev/null 2>&1 || need_apt+=("$pkg")
done
if [ ${#need_apt[@]} -gt 0 ] && command -v apt-get >/dev/null; then
  SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null && SUDO="sudo"
  $SUDO apt-get update -qq && $SUDO apt-get install -y -qq "${need_apt[@]}" || echo "warn: apt install failed for ${need_apt[*]} (continuing)"
fi

# 2. python venv with pinned versions
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
if ! "$VENV/bin/python" -c "import cadquery, OCP, ezdxf, matplotlib, trimesh, pypdf" 2>/dev/null; then
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q \
    cadquery==2.8.0 cadquery-ocp==7.9.3.1.1 ezdxf==1.4.5 matplotlib==3.11.2 numpy==2.5.5 pillow==13.0.0 trimesh==5.2.2 pypdf==7.1.1
fi

# 3. a font with Chinese/Japanese/Korean glyphs so names like FAS-10345_預設 print instead of boxes.
#    Optional (EPURE_CJK_FONT=0 skips it); without one, Latin text is unaffected and CJK shows as boxes.
has_cjk() {
  { find /usr/share/fonts /usr/local/share/fonts "$HOME/.local/share/fonts" "$HOME/.fonts" "$HERE/fonts" \
       \( -iname '*CJK*' -o -iname 'wqy-*' -o -iname 'DroidSansFallback*' -o -iname 'SourceHanSans*' \) 2>/dev/null || true; } | grep -q .
}
if [ "${EPURE_CJK_FONT:-1}" != "0" ] && ! has_cjk; then
  if command -v apt-get >/dev/null; then
    SUDO=""; [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null && SUDO="sudo"
    $SUDO apt-get install -y -qq fonts-noto-cjk >/dev/null 2>&1 || true
  fi
  if ! has_cjk; then    # no apt: fetch one 16 MB OpenType file (all CJK ideographs) into ./fonts
    mkdir -p "$HERE/fonts"
    if curl -fsSL --retry 2 -o "$HERE/fonts/NotoSansCJKsc-Regular.otf.part" \
         https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf; then
      mv "$HERE/fonts/NotoSansCJKsc-Regular.otf.part" "$HERE/fonts/NotoSansCJKsc-Regular.otf"
    else
      rm -f "$HERE/fonts/NotoSansCJKsc-Regular.otf.part"; echo "warn: no CJK font installed (Chinese/Japanese/Korean text will print as boxes)"
    fi
  fi
  "$VENV/bin/python" -c "from ezdxf.fonts import fonts; fonts.build_system_font_cache()" >/dev/null 2>&1 || true
fi

# 4. wrapper + smoke test
# The committed wrapper is relocatable; only write one if it's missing, so a
# git checkout stays clean and `git pull --ff-only` never trips over it.
if [ ! -f "$HERE/epure" ]; then
cat > "$HERE/epure" <<'WRAP'
#!/usr/bin/env bash
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$HERE/venv/bin/python" "$HERE/epure.py" "$@"
WRAP
fi
chmod +x "$HERE/epure"
"$VENV/bin/python" -c "import cadquery, OCP, ezdxf; print('epure ok: cadquery', cadquery.__version__, '| ezdxf', ezdxf.__version__)"
