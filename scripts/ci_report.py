"""CI 辅助：把 pytest 失败用例输出成 GitHub 注解，便于无日志权限时定位。"""

from __future__ import annotations

import xml.etree.ElementTree as ET


def main() -> int:
    try:
        tree = ET.parse("report.xml")
    except Exception as exc:  # noqa: BLE001
        print(f"::error title=pytest::无法解析 report.xml: {exc}")
        return 0
    failed = 0
    for case in tree.iter("testcase"):
        bad = case.find("failure") is not None or case.find("error") is not None
        if not bad:
            continue
        failed += 1
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        message = ""
        node = case.find("failure")
        if node is None:
            node = case.find("error")
        if node is not None and node.get("message"):
            message = node.get("message", "").splitlines()[0][:180]
        print(f"::error title=pytest failed::{name} {message}")
    print(f"::notice title=pytest::失败用例 {failed} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
