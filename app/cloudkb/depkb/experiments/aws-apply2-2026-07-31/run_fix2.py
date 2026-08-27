"""B4 보정 2 — internal 스킴에서도 서브넷 생략이 거부되는가.

lb→subnet의 생략 거부는 기본(internet-facing) 스킴에서만 측정돼 있었다.
이 호출은 자원을 만들지 않는다(생략이 거부되는 것이 가설). 결과는
results.json에 F2 스텝으로 병합한다.

실행: `python run_fix2.py`
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import HERE, aws  # noqa: E402

results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
res = aws(["elbv2", "create-load-balancer", "--name", "depkb2f2-nlb",
           "--type", "network", "--scheme", "internal"])
res.pop("_data", None)
results["steps"]["F2.internal-nlb-omit-subnets"] = res
tag = "OK" if res["ok"] else "/".join(res["errorCodes"])
print(f"F2.internal-nlb-omit-subnets      {tag}")
if res["ok"]:
    sys.exit("예상 밖 성공 — LB가 생겼다, 정리 필요")
results["ranAtFix2"] = datetime.now(UTC).isoformat(timespec="seconds")
(HERE / "results.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
