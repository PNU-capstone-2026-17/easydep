"""무인 측정 캠페인 — 몇 시간 동안 혼자 돌면서 **쌓이는** 측정.

## 왜 이런 것이 필요한가

측정이 자주 끊긴다. 이 저장소에서 하루 동안 겪은 것만:

  - 파이썬이 파이프 출력을 버퍼링해서, `timeout`이 죽이면 **부분 출력이 통째로 사라졌다**.
  - `grep`도 버퍼링한다 — `-u`를 써도 뒤에 붙은 grep이 먹었다.
  - 백그라운드 실행이 세 번 중단됐다.
  - 엔드포인트가 스로틀되면 호출당 3.6초가 **47초**가 된다(13배).

그래서 이 러너는 세 가지를 지킨다:

  1. **진행분을 즉시 파일에 쓴다**(줄 단위 append + flush).
  2. **이미 한 일은 다시 하지 않는다** — 각 단계가 자기 누적 파일을 읽고 남은 것만 한다.
  3. **조건을 섞지 않는다** — 단계마다 (반복 수, 명세 수)를 고정해 기록한다. 조건이 섞인
     표는 순위로 쓸 수 없다(그것 때문에 결론을 두 번 뒤집었다).

## 페이싱은 가정하지 않고 잰다

동시성 8에서 15콜이 400초를 넘겼는데, 동시성 2에서는 호출당 3.6초였다. 즉 **동시에 많이
던지는 것이 오히려 느리다.** 그래서 시작할 때 짧은 탐색으로 호출당 지연을 재고, 느리면
동시성을 더 낮춘다. 재는 값은 로그에 남는다 — 나중에 수치를 읽을 때 그 실행이 스로틀
상태였는지 알아야 한다.

## 무엇을 못 하나

**라벨은 만들지 못한다.** 라벨은 사람(또는 독립 평가자)이 붙여야 하고, LLM이 붙이면
검증자를 검증자로 채점하는 순환이 된다. 이 러너가 준비하는 것은 수치와, 라벨을 붙일
**눈가림 파일**까지다.
"""
from __future__ import annotations

import contextlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: 페이싱 탐색에 쓸 호출 수. 적게 던져 보고 결정한다.
_PROBE_CALLS = 2
#: 이 지연을 넘으면 스로틀로 보고 동시성을 내린다(초/호출).
_SLOW_CALL = 8.0


@dataclass
class Campaign:
    """한 캠페인의 상태. 모든 산출물은 `out_dir` 아래에 쌓인다."""

    out_dir: Path
    hours: float
    concurrency: int = 2
    started: float = 0.0

    def __post_init__(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.started = time.time()
        self.log_path = self.out_dir / "campaign.log"
        # **콘솔 인코딩이 캠페인을 죽이지 못하게 한다.** 윈도우 콘솔은 cp949라 `—`
        # 하나에 `print`가 UnicodeEncodeError를 낸다 — 실제로 3시간짜리 실행이 진행
        # 로그 한 줄 때문에 죽었다. 파일은 utf-8로 쓰고 있었으니 잃은 것은 순전히
        # 화면 출력 때문이다.
        with contextlib.suppress(Exception):  # 파이프·리다이렉트면 없을 수 있다
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

    # -- 기본기 ------------------------------------------------------------
    def log(self, message: str) -> None:
        """진행을 즉시 파일과 화면에 남긴다. **버퍼에 두지 않는다.**

        기록이 먼저다. 화면 출력이 실패해도 캠페인은 계속 간다 — 로그 한 줄이 몇 시간을
        무르게 하는 일은 이미 한 번 겪었다.
        """
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)

    def left(self) -> float:
        """남은 예산(초). 0 이하면 멈춘다."""
        return self.hours * 3600 - (time.time() - self.started)

    def spent(self) -> str:
        return f"{(time.time() - self.started) / 60:.0f}분 경과 · {self.left() / 60:.0f}분 남음"

    # -- 페이싱 ------------------------------------------------------------
    def measure_pace(self) -> float:
        """호출당 지연을 재고 동시성을 정한다. 재는 값도 로그에 남긴다."""
        from app.requirements.agent import validator
        from app.requirements.common import telemetry
        from app.requirements.config import settings
        from app.requirements.evaluation import seeded
        from app.requirements.knowledge import rules

        settings.spec_concurrency = self.concurrency
        artifact = seeded.clean_artifacts()[rules.WRITE_SPECIFICATIONS]
        start = time.time()
        with telemetry.run_scope("campaign-pace") as stats:
            for _ in range(_PROBE_CALLS):
                validator.review(
                    rules.WRITE_SPECIFICATIONS, artifact,
                    prefix="pace", source="campaign.pace",
                )
        data = stats.as_dict()
        # **요청 단위 벽시계로 잰다.** `llm_calls`로 나누면 지연을 과소평가한다 — 구조화
        # 출력이 폴백하면 논리 요청 하나가 여러 콜로 세지기 때문이다(실측에서 9s로 찍혔는데
        # 실제는 31s였다).
        per_request = (time.time() - start) / _PROBE_CALLS
        self.log(
            f"페이싱: 요청 {_PROBE_CALLS}건 · **요청당 {per_request:.1f}s** "
            f"(계측된 llm 호출 {data['llm_calls']}건 · 실패 {data['llm_failures']})"
        )
        per_call = per_request
        if per_call > _SLOW_CALL and self.concurrency > 1:
            self.concurrency = 1
            settings.spec_concurrency = 1
            self.log(f"느리다({per_call:.1f}s > {_SLOW_CALL}s) → 동시성 1로 내린다")
        return per_call


def _jsonl_ids(path: Path, key: str) -> set[str]:
    """누적 파일에 이미 있는 키들. 없으면 빈 집합."""
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            done.add(json.dumps(
                [json.loads(line).get(k) for k in key.split(",")], ensure_ascii=False
            ))
    return done


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        fh.flush()


def _stamped(row: dict, paths: list[str]) -> dict:
    """행마다 **어떤 프롬프트로 잰 것인지** 붙인다.

    캠페인은 몇 시간을 돈다. 그동안 규칙이나 프롬프트를 고치면 앞의 행과 뒤의 행이 다른
    것을 잰 것이 되는데, 지금까지 남는 조건은 반복 수·명세 수뿐이라 표만 봐서는 알 수
    없었다. 이 저장소에서 결론이 뒤집힌 원인은 거의 언제나 **조건이 섞인 것**이었다.

    `paths`로 **이 측정이 실제로 쓴 프롬프트만** 찍는다. 전부 찍으면 상관없는 프롬프트가
    바뀌었을 때 멀쩡한 측정이 무효로 표시된다 — 실제로 그렇게 5시간짜리 측정이 멈췄다
    (`prompts.fingerprint` docstring).
    """
    from app.requirements import prompts
    from app.requirements.agent.llm import build_llm

    # 클라이언트 상한도 조건이다. 2026-07-27에 600→90으로 내렸더니 **어떤 요청이 성공하는지가
    # 달라졌다**(9분대에 멈추던 요청이 실패·재시도로 바뀐다). 프롬프트가 같아도 이 값이 다르면
    # 같은 표에 넣을 수 없다.
    return row | {
        "prompts": prompts.fingerprint(paths),
        "client": {"timeout": getattr(build_llm(), "request_timeout", None)},
    }


# ---------------------------------------------------------------------------
# 단계들 — 각자 자기 누적 파일을 보고 남은 것만 한다
# ---------------------------------------------------------------------------
def phase_stability(c: Campaign, run_dirs: list[str], rule_ids: list[str],
                    repeats: int, limit: int) -> None:
    """규칙 × 도메인 단독 프로브. 조건(반복·명세수)을 고정해 기록한다.

    명세는 **고르게** 뽑는다(`sampling.even_sample`). 앞에서 자르면 실행의 앞쪽 유스케이스만
    재는데, 명세가 3~5개인 실행(PURE)은 상한이 안 걸리고 10~17개인 실행(내부 입력)은 걸려서
    **한쪽 표에만 위치 편향이 생긴다** — 그러면 코퍼스 비교가 코퍼스가 아니라 뽑기 차이를
    잰다. `limit`이 0 이하면 전부 잰다.
    """
    from app.requirements import prompts
    from app.requirements.evaluation import dataset, semantic
    from app.requirements.evaluation.sampling import even_sample

    out = c.out_dir / "probe-rank.jsonl"
    done = _jsonl_ids(out, "domain,rule_id,spec_id")
    # **작업 단위는 (도메인, 규칙, 명세) 하나다.** 예전엔 (도메인, 규칙)이었는데 그건
    # 5명세 × 5반복 = 25콜짜리 덩어리라, 중간에 죽으면 그 전부가 사라졌다(실측에서 8분을
    # 그렇게 잃었다). 잘게 쪼개면 죽어도 명세 하나(반복 수만큼)만 잃는다.
    for run_dir in run_dirs:
        domain = dataset.domain_of(run_dir)
        payloads = even_sample(semantic.payloads_from_run(run_dir), limit)
        for rule_id in rule_ids:
            for spec_id, payload in payloads:
                key = json.dumps([domain, rule_id, spec_id], ensure_ascii=False)
                if key in done:
                    continue
                if c.left() <= 0:
                    c.log("예산 소진 — 프로브 단계 중단(남은 것은 다음 실행이 이어서 한다)")
                    return
                row = semantic.probe_rule(rule_id, [(spec_id, payload)], repeats=repeats)
                row |= {"domain": domain, "spec_id": spec_id, "phase": "probe"}
                _append(out, _stamped(row, [prompts.PROBE]))
                c.log(
                    f"프로브 {domain}/{rule_id.split('.')[-1]}/{spec_id}: "
                    f"항상 {row['always']} 때때로 {row['sometimes']} · {c.spent()}"
                )


def phase_dataset_score(c: Campaign, labels_path: Path, repeats: int) -> None:
    """라벨 대비 모델 판정. 항목마다 즉시 쌓이고 이미 물어본 것은 건너뛴다."""
    from app.requirements.evaluation import dataset

    labelled = json.loads(labels_path.read_text(encoding="utf-8"))
    verdicts = c.out_dir / "verdicts.jsonl"
    while c.left() > 0:
        report = dataset.score(labelled, repeats=repeats, verdict_file=verdicts, budget=1)
        if report["not_yet_asked"] == 0:
            c.log(f"채점 완료: {report} ")
            return
        c.log(f"채점 {report['scored']}건 · 남은 {report['not_yet_asked']}건 · {c.spent()}")
    c.log("예산 소진 — 채점 중단(다음 실행이 이어서 한다)")


def phase_pure_runs(c: Campaign, names: list[str], limit: int | None) -> None:
    """PURE 외부 입력으로 파이프라인을 돌린다. 도메인마다 한 번, 이미 한 것은 건너뛴다."""
    from app.requirements import prompts
    from app.requirements.evaluation import pure
    from app.requirements.runner import persist_run, run_pipeline

    out = c.out_dir / "pure-runs.jsonl"
    done = _jsonl_ids(out, "document")
    for name in names:
        if json.dumps([name], ensure_ascii=False) in done:
            continue
        if c.left() <= 0:
            c.log("예산 소진 — PURE 실행 중단")
            return
        payload = pure.extract(name, limit=limit)
        c.log(f"PURE 실행 시작 {payload['name']} (요구 {len(payload['classified'])}건)")
        state = run_pipeline(payload["classified"])
        run_dir = persist_run(
            payload, state, dataset_name=payload["name"],
            artifact_root=c.out_dir / "pure-artifacts",
        )
        _append(out, _stamped({
            "document": name, "dataset": payload["name"], "run_dir": str(run_dir),
            "n_use_cases": len(state.get("use_cases", [])),
            "n_specs": len(state.get("use_case_specs", [])),
        }, [prompts.GENERATION]))
        c.log(f"PURE 실행 완료 {payload['name']} → {run_dir.name} · {c.spent()}")


def produced_run_dirs(c: Campaign) -> list[str]:
    """이 캠페인이 만든 실행 디렉터리들(PURE·내부 입력 양쪽).

    **단계가 서로를 먹여야 무인이 된다.** 생성 단계가 실행을 만들고 `stability`가 그 실행을
    재는데, 실행 목록을 명령줄로만 받으면 사람이 중간에 들어와 경로를 넘겨야 한다 —
    "몇 시간 혼자 돈다"는 이 러너의 전제가 거기서 깨진다. 누적 파일에서 읽으므로
    **이전 실행이 만든 것도 이어받는다.**
    """
    dirs = []
    for name in ("pure-runs.jsonl", "input-runs.jsonl"):
        out = c.out_dir / name
        if not out.exists():
            continue
        for line in out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            run_dir = json.loads(line).get("run_dir")
            # 산출물이 지워졌을 수 있다 — 없는 경로를 프로브에 넘기면 그 도메인이 통째로 죽는다.
            if run_dir and Path(run_dir).exists():
                dirs.append(run_dir)
    return dirs


def phase_input_runs(c: Campaign, names: list[str]) -> None:
    """`inputs/*.json`(우리가 쓴 입력)으로 파이프라인을 돌린다.

    PURE와 **같은 프롬프트 판·같은 클라이언트 상한**으로 지금 다시 만드는 것이 요점이다.
    §9의 내부 입력 수치는 옛 판이고 네 규칙은 조합 프롬프트에서 나왔다 — 그대로 PURE 표와
    나란히 놓으면 코퍼스가 바꾼 것과 판이 바꾼 것이 섞인다.
    """
    from app.requirements import prompts
    from app.requirements.runner import load_input, persist_run, run_pipeline

    out = c.out_dir / "input-runs.jsonl"
    done = _jsonl_ids(out, "dataset")
    for name in names:
        if json.dumps([name], ensure_ascii=False) in done:
            continue
        if c.left() <= 0:
            c.log("예산 소진 — 내부 입력 실행 중단")
            return
        payload = load_input(name)
        c.log(f"내부 실행 시작 {name} (요구 {len(payload.get('classified', []))}건)")
        state = run_pipeline(payload["classified"])
        run_dir = persist_run(
            payload, state, dataset_name=name,
            artifact_root=c.out_dir / "input-artifacts",
        )
        _append(out, _stamped({
            "dataset": name, "run_dir": str(run_dir),
            "n_use_cases": len(state.get("use_cases", [])),
            "n_specs": len(state.get("use_case_specs", [])),
        }, [prompts.GENERATION]))
        c.log(f"내부 실행 완료 {name} → {run_dir.name} · {c.spent()}")


PHASES: dict[str, Callable] = {
    "stability": phase_stability,
    "score": phase_dataset_score,
    "pure": phase_pure_runs,
    "inputs": phase_input_runs,
}


def run(
    out_dir: Path,
    hours: float,
    run_dirs: list[str],
    labels_path: Path | None,
    pure_docs: list[str],
    repeats: int,
    limit: int,
    concurrency: int,
    phases: list[str],
    pure_limit: int = 30,
    input_names: list[str] | None = None,
) -> int:
    """캠페인 한 번. 단계를 **우선순위 순서로** 돌린다.

    순서에 뜻이 있다: ① 규칙 개정이 흔들림을 줄였는지(오늘 한 변경의 검증) → ② 정밀도·재현율
    → ③ 외부 입력(PURE) 확보. 앞의 것이 뒤의 것보다 값싸고, 답을 기다리는 질문이 더 앞에 있다.
    """
    from app.requirements.knowledge import rules

    input_names = input_names or []
    c = Campaign(out_dir=out_dir, hours=hours, concurrency=concurrency)
    c.log("=" * 70)
    c.log(f"캠페인 시작 · 예산 {hours}시간 · 동시성 {concurrency} · 산출물 {out_dir}")
    c.log(f"도메인 {len(run_dirs)}종 · 조건(반복 {repeats}, 명세 {limit})")
    # 프롬프트 판을 **시작할 때 한 번** 남긴다. 행마다 붙는 값과 다르면 실행 도중에
    # 코드가 바뀐 것이고, 그 표는 순위로 쓸 수 없다.
    from app.requirements import prompts
    c.log(f"프롬프트 판: {prompts.fingerprint()}")
    c.measure_pace()

    rule_ids = [r.id for r in rules.judged_by(rules.WRITE_SPECIFICATIONS,
                                              rules.JUDGED_VALIDATOR)]

    # 단계 순서는 **명령줄이 정한다.** 지연이 20배로 요동치므로, 무엇을 먼저 확보할지는
    # 그때그때 다르다 — 외부 근거(PURE)가 급하면 그걸 앞에 두고, 오늘 한 변경의 검증이
    # 급하면 프로브를 앞에 둔다.
    for index, phase in enumerate(phases, start=1):
        head = f"[{index}/{len(phases)}]"
        if c.left() <= 0:
            c.log(f"{head} 건너뜀 — 예산 소진")
            continue
        if phase == "stability":
            # 명령줄로 받은 실행 + **이 캠페인이 만든 PURE 실행**. 뒤엣것이 있어야
            # `pure stability` 한 줄로 "외부 입력을 돌리고 그 결과를 잰다"가 끝난다.
            targets = list(dict.fromkeys([*run_dirs, *produced_run_dirs(c)]))
            if not targets:
                c.log(f"{head} 건너뜀 — 잴 실행이 없다(--run-dirs 도 없고 만든 실행도 없다)")
                continue
            c.log(f"{head} 규칙 × 도메인 단독 프로브 — 규칙 {len(rule_ids)}개 · 실행 {len(targets)}종")
            phase_stability(c, targets, rule_ids, repeats=repeats, limit=limit)
        elif phase == "score":
            if labels_path and labels_path.exists():
                c.log(f"{head} 라벨 대비 채점")
                phase_dataset_score(c, labels_path, repeats=repeats)
            else:
                c.log(f"{head} 건너뜀 — 라벨 파일이 없다")
        elif phase == "pure":
            c.log(f"{head} PURE 외부 입력 실행 — 문서 {len(pure_docs)}건 · 문서당 요구 {pure_limit}건")
            phase_pure_runs(c, pure_docs, limit=pure_limit)
        elif phase == "inputs":
            c.log(f"{head} 내부 입력 실행 — 데이터셋 {len(input_names)}종")
            phase_input_runs(c, input_names)
        else:
            c.log(f"{head} 알 수 없는 단계 {phase!r} — 건너뛴다")

    c.log(f"캠페인 종료 · {c.spent()}")
    c.log("이어서 하려면 같은 --out-dir로 다시 돌린다. 이미 한 일은 건너뛴다.")
    return 0
