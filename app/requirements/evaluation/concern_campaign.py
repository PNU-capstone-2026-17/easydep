"""클라우드 관심사 링크 층의 무인 측정 — 열린 질문 넷을 한 격자로 답한다.

    python -m app.requirements.evaluation concerns --hours 8 --repeats 10

## 무엇을 재는가

  1. **LLM 층이 결정론 층 대비 얼마나 더 줍는가** — 열쇠말이 못 본 링크의 비율.
  2. **1표가 얼마나 흔들리는가** — 같은 (도메인, 관심사)를 반복 질의했을 때의 일치율.
  3. **한 번에 묻기 vs 나눠 묻기** — 관심사 29개·5,209자 프롬프트의 위험(§9는 규칙
     6개만으로도 안정 판정이 0이 된 것을 이미 쟀다).
  4. **미분화 7쌍이 개념 문제인가 열쇠말 문제인가** — 열쇠말이 아닌 LLM 링크로 다시 재면
     갈리는지.

## 표는 한 장씩 기록하고 다수결은 나중에 계산한다

`concern_linker_votes`를 1로 고정하고 **1표를 한 행으로** 남긴다. 그러면 같은 데이터로
"1표가 얼마나 흔들리는가"와 "3표 다수결이면 어떻게 되는가"를 둘 다 낼 수 있다 — 다수결을
러너 안에서 해 버리면 흔들림 자체가 사라져 보이고, 문턱을 바꿔 보려면 다시 돌려야 한다.

## 배관 규율 (이 저장소가 물린 것들)

  - **기록이 출력보다 먼저.** 줄 단위 append + flush. `print`가 cp949로 죽어서 3시간
    실행이 날아간 적이 있다 → stdout을 utf-8로 재설정한다.
  - **이미 한 일은 다시 하지 않는다.** 행마다 `cell` 키를 두고, 시작할 때 읽어 건너뛴다.
    루프가 끊겨도 이어서 돈다.
  - **조건을 섞지 않는다.** 행마다 청크 크기·프롬프트 판·모델을 함께 적는다. 조건이 섞인
    표는 순위로 쓸 수 없다(그것 때문에 결론을 두 번 뒤집었다).
  - **가장 값싸고 정보량 큰 조건부터.** 시간이 모자라 중간에 끊겨도 답이 남는 순서다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.requirements import prompts
from app.requirements.agent.steps import step_cloud
from app.requirements.common.console import use_utf8_stdout
from app.requirements.config import settings
from app.requirements.evaluation import jsonl
from app.requirements.knowledge import concerns

_ROOT = Path(__file__).resolve().parents[3]
INPUTS_DIR = _ROOT / "inputs"

#: 조건 순서 = **답이 먼저 남는 순서.** 0(한 번에)이 지금 기본값이라 기준선이고,
#: 10은 나눠 묻기의 첫 갈래, 1은 가장 비싸다(관심사마다 한 번).
CHUNK_SIZES = (0, 10, 1)


def _log(path: Path, message: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
    print(line, flush=True)


def _done_cells(path: Path) -> set[str]:
    """이미 잰 칸들. **잘린 마지막 줄은 `jsonl.rows`가 건너뛴다** — 그 칸은 다시 돈다."""
    return {row["cell"] for row in jsonl.rows(path) if "cell" in row}


def load_domains() -> list[tuple[str, list[dict]]]:
    """`inputs/*.json`의 (이름, 분류된 요구사항). 이미 분류돼 있어 step1이 필요 없다."""
    out = []
    for path in sorted(INPUTS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("classified"):
            out.append((path.stem, data["classified"]))
    return out


def one_ballot(classified: list[dict], chunk: int) -> dict:
    """한 표를 받아 결과를 행으로 만든다. 실패는 예외가 아니라 행의 값이다."""
    settings.concern_linker_chunk = chunk
    started = time.perf_counter()
    linkage = step_cloud._one_ballot(classified)
    elapsed = round(time.perf_counter() - started, 2)

    known = {item["id"] for item in classified}
    links: dict[str, list[str]] = {}
    judged: list[str] = []
    invented = 0
    if linkage is not None:
        for link in linkage.links:
            if link.concern_id not in concerns.BY_ID:
                invented += 1
                continue
            if link.concern_id in links:
                continue  # 같은 관심사를 두 번 답했다 — 첫 답만 센다
            judged.append(link.concern_id)
            links[link.concern_id] = sorted(
                r for r in link.requirement_ids if r in known
            )
    return {
        "elapsed_s": elapsed,
        "answered": linkage is not None,
        "links": links,
        "judged": sorted(judged),
        "invented_concerns": invented,
    }


def run(out_dir: Path, hours: float, repeats: int) -> int:
    use_utf8_stdout()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path, log_path = out_dir / "ballots.jsonl", out_dir / "campaign.log"

    # 1표씩 기록한다 — 다수결은 분석에서 계산한다(모듈 docstring).
    settings.concern_linker_llm = True
    settings.concern_linker_votes = 1

    domains = load_domains()
    done = _done_cells(rows_path)
    fingerprint = prompts.fingerprint()
    deadline = time.time() + hours * 3600

    _log(log_path, f"관심사 {len(concerns.CONCERNS)}건 · 도메인 {len(domains)}종 "
                   f"· 조건 {CHUNK_SIZES} · 반복 {repeats}")
    _log(log_path, f"프롬프트 판 {fingerprint['concerns']} · 모델 {settings.model}")
    _log(log_path, f"이미 끝난 칸 {len(done)}개 — 건너뛴다")

    planned = len(domains) * len(CHUNK_SIZES) * repeats
    ran = failed = 0
    for chunk in CHUNK_SIZES:            # 값싼 조건부터
        for repeat in range(repeats):
            for name, classified in domains:
                cell = f"{name}|chunk{chunk}|r{repeat}"
                if cell in done:
                    continue
                if time.time() > deadline:
                    _log(log_path, f"시간 종료 — {ran}칸 실행, 남은 계획 {planned - len(done) - ran}칸")
                    return 0
                result = one_ballot(classified, chunk)
                jsonl.append(rows_path, {
                    "cell": cell,
                    "domain": name,
                    "chunk": chunk,
                    "repeat": repeat,
                    "requirements": len(classified),
                    "model": settings.model,
                    "prompt_concerns": fingerprint["concerns"],
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    **result,
                })
                ran += 1
                if not result["answered"]:
                    failed += 1
                if ran % 10 == 0:
                    left = (deadline - time.time()) / 60
                    _log(log_path, f"{ran}칸 (chunk={chunk}) · 실패 {failed} "
                                   f"· 남은 시간 {left:.0f}분")
    _log(log_path, f"격자 완주 — {ran}칸 실행, 실패 {failed}")
    return 0
