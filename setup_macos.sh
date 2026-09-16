#!/usr/bin/env bash
# macOS 开发环境准备（对应 Windows 上的 setup.bat）
#
# 这个脚本只是让代码在 macOS 上**跑起来开发**，不是 macOS 发行版：
# 全局热键、剪贴板历史、窗口智能选区这几块依赖 Win32，在 macOS 上是关掉的。
#
# 依赖方面：
#   - pywin32 等 Windows 专用包在 requirements.txt 里已用平台标记标出，pip 会自动跳过
#   - j-ppocr / j-stitch 在 PyPI 上只有 Windows wheel，这里从 rust_libs/ 本地构建，
#     产物直接放进 site-packages 并改成扩展模块名，效果和 pip 装出来的一样
#   - j-gif 的 Windows 部分（GDI BitBlt 抓屏）已按平台收口：macOS 上抓帧改用 Python
#     的 mss，压缩/存储/导出仍然走 Rust 的同一个 FrameStore
#   - j-clipboard 在这边也能用：它的底层 clipboard-rs 是跨平台的，crate 里那些
#     `cfg(not(windows))` 分支只是省掉了 Windows 专有的优化（按白名单直读格式）
#     和"来源应用"名称，文字/图片/文件与历史存储照常工作
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3.11}"
VENV="${VENV:-venv311}"

# macOS 上 pyo3 扩展要用 dynamic_lookup 链接，否则链接期找不到 __Py_* 符号
RUST_LINK_ARGS=(-C link-arg=-undefined -C link-arg=dynamic_lookup)
RUST_CRATES=(ppocr_rust longstitch pyclipboard gifrecorder)

echo "== 1/3 创建虚拟环境 $VENV =="
"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install -q --upgrade pip

echo "== 2/3 安装 Python 依赖 =="
"$VENV/bin/python" -m pip install -r requirements-dev.txt

echo "== 3/3 本地构建 Rust 扩展 =="
SITE_PACKAGES=$("$VENV/bin/python" -c "import site; print(site.getsitepackages()[0])")
for crate in "${RUST_CRATES[@]}"; do
    echo "-- $crate"
    cargo rustc --release -p "$crate" \
        --manifest-path rust_libs/Cargo.toml -- "${RUST_LINK_ARGS[@]}"
    cp "rust_libs/target/release/lib$crate.dylib" "$SITE_PACKAGES/$crate.abi3.so"
    "$VENV/bin/python" -c "import $crate" && echo "   import $crate ok"
done

cat <<EOF

完成。运行：
    cd main && ../$VENV/bin/python main_app.py

跑测试（macOS 上不要设 QT_QPA_PLATFORM=offscreen，无边框窗口在离屏平台会崩）：
    $VENV/bin/python -m pytest main/tests -c main/tests/pytest.ini
EOF
