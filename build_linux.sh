#!/usr/bin/env bash
# Build RCSBridge + RCSConfigEditor for Linux (Ubuntu 22.04 target).
set -euo pipefail
cd "$(dirname "$0")"

OUT="${OUT_DIR:-dist_ubuntu22}"
WORK="${WORK_DIR:-build/pyi-linux}"

echo "[1/4] ensure pyinstaller..."
python -m pip install -q "pyinstaller>=6.0"

rm -rf "$OUT" "$WORK"
mkdir -p "$OUT/config"

echo "[2/4] build RCSBridge..."
python -m PyInstaller --noconfirm --clean --onefile --console \
  --name RCSBridge \
  --paths src \
  --additional-hooks-dir packaging/hooks \
  --hidden-import config_loader \
  --hidden-import rcs_client \
  --hidden-import tcp_parser \
  --hidden-import workflow \
  --hidden-import route_resolve \
  --hidden-import bridge \
  --distpath "$OUT" \
  --workpath "$WORK" \
  --specpath "$WORK" \
  main.py

echo "[3/4] build RCSConfigEditor..."
python -m PyInstaller --noconfirm --clean --onefile --windowed \
  --name RCSConfigEditor \
  --paths config_editor \
  --hidden-import config_io \
  --hidden-import gui \
  --distpath "$OUT" \
  --workpath "$WORK" \
  --specpath "$WORK" \
  config_editor/main.py

echo "[4/4] copy config next to binaries..."
cp -f config/config.example.config "$OUT/config/"
cp -f config/payload_arrive.json "$OUT/config/"
cp -f config/payload_leave.json "$OUT/config/"
if [[ ! -f "$OUT/config/config.config" ]]; then
  cp -f config/config.example.config "$OUT/config/config.config"
fi

cat > "$OUT/README.txt" <<'EOF'
RCS Linux 发布目录 (Ubuntu 22.04)
================================

  RCSBridge           主程序（TCP → RCS）
  RCSConfigEditor     路由/登录配置编辑器（需图形界面 + tk）
  config/             配置与 payload

运行示例:
  chmod +x RCSBridge RCSConfigEditor
  ./RCSBridge
  ./RCSConfigEditor

无桌面环境时 ConfigEditor 可能无法启动；Bridge 可在纯服务器运行。
EOF

chmod +x "$OUT/RCSBridge" "$OUT/RCSConfigEditor"

echo
echo "Done. Output under: $OUT/"
ls -lh "$OUT"
