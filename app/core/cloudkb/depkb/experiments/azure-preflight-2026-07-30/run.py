"""P5a 실행기 — 템플릿마다 validate와 what-if를 실제 컨트롤 플레인에 묻는다.

자원은 만들지 않는다(두 연산 모두 preflight). 결과는 `results.json`에 원문
발췌와 함께 남는다 — 이 파일이 측정 기록이고, 재실행하면 그 시점의 새 측정이다
(캐시 사영이 아니므로 재계산 정합 검사 대상이 아니다).

실행: `python run.py <resource-group>`
"""

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
AZ = shutil.which("az")

_CODE = re.compile(r'"code":\s*"([^"]+)"')


def preflight(kind: str, template: Path, rg: str) -> dict:
    cmd = [AZ, "deployment", "group", kind, "-g", rg,
           "--template-file", str(template), "-o", "json",
           "--only-show-errors"]
    if kind == "what-if":
        cmd += ["--no-pretty-print"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    text = (r.stderr or "") + (r.stdout or "")
    codes = list(dict.fromkeys(_CODE.findall(text)))
    return {
        "ok": r.returncode == 0,
        "errorCodes": codes,
        "excerpt": text.strip().replace("\r", "")[:600],
    }


def main() -> None:
    rg = sys.argv[1]
    results = {
        "_note": (
            "azure preflight 측정 기록(P5a). ok=True는 preflight 통과일 뿐 "
            "의존 부재의 증거가 아니다 — 거부만이 증거다(계획 T7). "
            "자원 생성 없음(validate·what-if만)."
        ),
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resourceGroup": rg,
        "tests": {},
    }
    for template in sorted((HERE / "templates").glob("*.json")):
        name = template.stem
        entry = {"validate": preflight("validate", template, rg)}
        entry["what-if"] = preflight("what-if", template, rg)
        results["tests"][name] = entry
        v, w = entry["validate"], entry["what-if"]
        print(f"{name:26} validate={'PASS' if v['ok'] else '/'.join(v['errorCodes']) or 'FAIL'}"
              f"  what-if={'PASS' if w['ok'] else '/'.join(w['errorCodes']) or 'FAIL'}")
    (HERE / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
