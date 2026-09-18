# -*- coding: utf-8 -*-
"""
compile_translations.py - 编译翻译文件

将 .ts 翻译源文件编译成 .qm 二进制文件。

使用方法:
    python compile_translations.py

需要安装 PySide6（已包含 pyside6-lrelease 工具）:
    pip install PySide6-Essentials
"""
import subprocess
from pathlib import Path


def find_lrelease():
    """查找 lrelease 工具路径"""
    # 尝试常见路径（优先 PySide6 的 pyside6-lrelease）
    candidates = [
        "pyside6-lrelease",  # PySide6 版本
        "lrelease",          # PATH 中
        "lrelease6",         # 备用
    ]

    # 当前解释器所在环境的 bin/（POSIX）与 Scripts/（Windows）：在哪个 venv 里跑就用哪个
    import sys

    for bin_dir in (Path(sys.executable).parent,):
        for name in ("pyside6-lrelease", "lrelease", "lrelease6",
                     "pyside6-lrelease.exe", "lrelease.exe", "lrelease6.exe"):
            candidate = bin_dir / name
            if candidate.exists():
                candidates.insert(0, str(candidate))

    # 仓库里可能存在的虚拟环境（老写法：只认 Windows 的 Scripts 布局）
    for venv_name in ("venv311", "venv", "venv39"):
        venv_dir = Path(__file__).parent.parent / venv_name
        for sub in ("Scripts", "bin"):
            venv_scripts = venv_dir / sub
            if venv_scripts.exists():
                candidates.extend([
                    str(venv_scripts / "pyside6-lrelease"),
                    str(venv_scripts / "pyside6-lrelease.exe"),
                    str(venv_scripts / "lrelease"),
                    str(venv_scripts / "lrelease.exe"),
                    str(venv_scripts / "lrelease6.exe"),
                ])

    # 尝试在 site-packages 中查找 PySide6 的 Qt6/bin
    try:
        import PySide6
        pyside6_path = Path(PySide6.__file__).parent
        qt_bin = pyside6_path / "Qt" / "bin"
        if qt_bin.exists():
            candidates.append(str(qt_bin / "lrelease.exe"))
            candidates.append(str(qt_bin / "lrelease"))
        # PySide6 本身附带 lrelease（macOS/Linux 上就是这个名字）
        for name in ("lrelease", "lrelease.exe"):
            lrelease_direct = pyside6_path / name
            if lrelease_direct.exists():
                candidates.insert(0, str(lrelease_direct))
    except ImportError:
        pass

    for cmd in candidates:
        try:
            result = subprocess.run([cmd, "-version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 or "lrelease" in result.stdout.lower() or "lrelease" in result.stderr.lower():
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            continue

    return None


def compile_ts_files():
    """编译所有 .ts/.xml 翻译文件"""
    translations_dir = Path(__file__).parent / "translations"
    
    if not translations_dir.exists():
        print(f"[ERROR] Translations directory not found: {translations_dir}")
        return False

    # 支持 .ts 和 .xml 后缀的翻译文件
    ts_files = list(translations_dir.glob("*.ts")) + list(translations_dir.glob("*.xml"))

    if not ts_files:
        print(f"[ERROR] No .ts or .xml files found: {translations_dir}")
        return False

    print(f"[INFO] Translations directory: {translations_dir}")
    print(f"[INFO] Found {len(ts_files)} .ts/.xml file(s)")

    # 查找 lrelease
    lrelease = find_lrelease()

    if lrelease:
        print(f"[INFO] Using lrelease: {lrelease}")

        for ts_file in ts_files:
            qm_file = ts_file.with_suffix(".qm")
            print(f"\n[INFO] Compiling: {ts_file.name} -> {qm_file.name}")

            try:
                result = subprocess.run(
                    [lrelease, str(ts_file), "-qm", str(qm_file)],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    print("   [OK] Success")
                else:
                    print(f"   [ERROR] Failed: {result.stderr}")
            except Exception as e:
                print(f"   [ERROR] Error: {e}")
    else:
        print("\n[ERROR] 找不到 lrelease，无法编译翻译。")
        print("   装法：pip install PySide6-Essentials（PySide6 自带 pyside6-lrelease）")
        print("   在 venv 里跑本脚本时，会用该 venv 的同名可执行文件。")
        # 刻意不写占位文件：仓库里已有编译好的 .qm，用 13 字节的占位文件覆盖等于
        # 把所有译文删掉（曾经真的这么干过），而且从输出上看像是「编译成功」。
        existing = [ts.with_suffix(".qm") for ts in ts_files if ts.with_suffix(".qm").exists()]
        if existing:
            print(f"[WARN] 保留已有的 {len(existing)} 个 .qm 不动（避免把译文覆盖成空文件）")
        return False

    print("\n[OK] Done!")
    return True


if __name__ == "__main__":
    compile_ts_files()
 