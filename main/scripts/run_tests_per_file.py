"""逐文件独立进程跑测试，并把结果落成 JSON 便于跨步骤比较。

为什么不用一次性跑全套：macOS 上 pytest 会话拆除时会在 GC 里段错误（Qt/ObjC 析构顺序），
崩溃点随文件组合漂移，同一份代码每次跑到的位置都不一样。所以：
  - 每个文件一个进程，保证结果与组合无关；
  - 崩过的文件重试若干次，只有每次都崩才算「稳定失败」，否则记为「偶发」；
  - 输出 JSON，后续步骤用 diff 比较，判断有没有引入新的失败。

用法：
    ./venv311/bin/python main/scripts/run_tests_per_file.py out.json [--retries 3]
    ./venv311/bin/python main/scripts/run_tests_per_file.py --diff out.json new.json
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

TESTS_DIR = pathlib.Path("main/tests")
PYTHON = "./venv311/bin/python"
SUMMARY_RE = re.compile(r"(\d+) failed[^\n]*?(\d+) passed|(\d+) passed|(\d+) skipped")


def run_one(path: str) -> tuple[int, int, int, int]:
    """返回 (退出码, 失败数, 通过数, 跳过数)。"""
    r = subprocess.run(
        [PYTHON, "-m", "pytest", path, "-c", "main/tests/pytest.ini",
         "-p", "no:cacheprovider", "--tb=no", "-q"],
        capture_output=True, text=True,
    )
    failed = passed = skipped = 0
    for line in r.stdout.splitlines():
        m = re.fullmatch(r".*?(\d+) failed.*?(\d+) passed.*", line.strip())
        if m:
            failed, passed = int(m.group(1)), int(m.group(2))
            break
        m = re.fullmatch(r".*?(\d+) passed.*", line.strip())
        if m:
            passed = int(m.group(1))
            break
    m = re.search(r"(\d+) skipped", r.stdout)
    if m:
        skipped = int(m.group(1))
    return r.returncode, failed, passed, skipped


def collect(retries: int) -> dict:
    result = {}
    files = sorted(p.as_posix() for p in TESTS_DIR.glob("test_*.py"))
    for i, path in enumerate(files, 1):
        code, failed, passed, skipped = run_one(path)
        flaky = False
        if code < 0:  # 被信号杀掉：重试，只有每次都崩才算稳定
            stable = True
            for _ in range(retries - 1):
                c2, f2, p2, s2 = run_one(path)
                if c2 >= 0:
                    stable = False
                    code, failed, passed, skipped = c2, f2, p2, s2
                    break
            flaky = not stable
        result[path] = {
            "exit": code, "failed": failed, "passed": passed,
            "skipped": skipped, "crash_flaky": flaky,
        }
        flag = "CRASH" if code < 0 else ("FAIL" if failed else "ok")
        if flag != "ok" or flaky:
            print(f"[{i:3d}/{len(files)}] {flag:5s} {path} "
                  f"F={failed} P={passed} flaky={flaky}", flush=True)
    return result


def diff(old: dict, new: dict) -> int:
    problems = 0
    for path, cur in sorted(new.items()):
        prev = old.get(path)
        if prev is None:
            print(f"[新增文件] {path} F={cur['failed']} P={cur['passed']}")
            continue
        if cur["failed"] > prev["failed"]:
            print(f"[失败变多] {path}: {prev['failed']} -> {cur['failed']}")
            problems += 1
        if prev["exit"] >= 0 and cur["exit"] < 0:
            print(f"[新增崩溃] {path}: exit {prev['exit']} -> {cur['exit']}")
            problems += 1
    for path in old:
        if path not in new:
            print(f"[文件消失] {path}")
    tot = lambda d, k: sum(v[k] for v in d.values())
    print(f"\n旧: 通过={tot(old,'passed')} 失败={tot(old,'failed')} 跳过={tot(old,'skipped')}")
    print(f"新: 通过={tot(new,'passed')} 失败={tot(new,'failed')} 跳过={tot(new,'skipped')}")
    print(f"回归项={problems}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"))
    args = ap.parse_args()

    if args.diff:
        old = json.loads(pathlib.Path(args.diff[0]).read_text())
        new = json.loads(pathlib.Path(args.diff[1]).read_text())
        return 1 if diff(old, new) else 0

    if not args.out:
        ap.error("需要输出文件，或用 --diff OLD NEW")
    result = collect(args.retries)
    pathlib.Path(args.out).write_text(json.dumps(result, indent=1, ensure_ascii=False))
    tot = lambda k: sum(v[k] for v in result.values())
    stable_crash = [p for p, v in result.items() if v["exit"] < 0 and not v["crash_flaky"]]
    flaky = [p for p, v in result.items() if v["crash_flaky"]]
    print(f"\n文件数={len(result)} 通过={tot('passed')} 失败={tot('failed')} "
          f"跳过={tot('skipped')}")
    print(f"稳定崩溃文件={len(stable_crash)} {[p.split('/')[-1] for p in stable_crash]}")
    print(f"偶发崩溃文件={len(flaky)} {[p.split('/')[-1] for p in flaky]}")
    print(f"失败文件={sorted(p.split('/')[-1] for p, v in result.items() if v['failed'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
