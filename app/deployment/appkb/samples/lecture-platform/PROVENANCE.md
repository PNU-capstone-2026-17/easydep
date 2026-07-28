# 출처 — 이 한 벌은 어디서 나왔나

**실물입니다.** 손으로 쓴 것이 하나도 없습니다 — 씨앗 문장(`INPUT.md` ·
`requirements.txt` · `constraints.txt`)만 사람이 썼고, `requirements/`와 `design/`은
전부 에이전트가 낸 것을 그대로 저장한 것입니다. 고쳐 쓰지 않았습니다.

시각은 전부 UTC입니다(파일의 `RUN.json`과 같은 기준).

## 누가 · 언제 · 무엇으로

| | 요구사항 | 설계 |
|---|---|---|
| 만든 것 | `app/requirements` 에이전트 | `app/design` 에이전트(팀원 코드) |
| 돌린 도구 | `tools/build_sample` | `tools/build_design` |
| 언제 | 2026-07-28 17:10:28Z ~ 17:13:05Z | 2026-07-28, 3회로 나눠(아래) |
| 모델 | `openai/gpt-oss-120b` (NIM) | 같음 |
| 실행 id | thread `4e424032-3575-4a4e-aae5-6162df39f4dd` | — |
| 입력 | `requirements.txt` 14문장 + `constraints.txt` | 위 `requirements/` 산출물 |

**어느 커밋에서 돌았는지는 기록이 없습니다.** 그때 두 도구가 커밋 해시를 안 남겼고,
없는 것을 뒤늦게 지어낼 수 없어 빈 채로 둡니다. 이후 실행부터는 `RUN.json`의 `code`
칸에 커밋과 `dirty` 여부가 남습니다(`tools/provenance.py`). 다만 이 표본을 만든
도구들 자체가 **아직 커밋되지 않은 상태**였다는 것은 확실합니다.

## 설계는 3회로 나뉘어 돌았다

`--only`로 단계를 나눠 돌렸고, 그 사이 `MAX_REVISION_ATTEMPTS` 상한을 넣었습니다
(시퀀스 단계가 무제한 재질의로 40분을 돌던 것 — `build_design`의 주석).

| 단계 | 파일 시각(KST) | 문법 검증 |
|---|---|---|
| class_diagram | 07-29 02:21 | **기록 없음** |
| sequence_diagram | 07-29 03:02 | **기록 없음** |
| api_spec | 07-29 03:04 | 통과 |
| erd | 07-29 03:04 | **실패** — `PlantUML syntax check failed.` (재질의 상한 1회 소진) |

앞의 두 단계 기록은 **덮여서 사라졌습니다** — 당시 `build_design`이 매 실행마다
`design/RUN.json`을 통째로 다시 썼기 때문입니다. 되살릴 수 없어 "없음"으로 둡니다.
같은 일이 다시 나지 않도록 그 도구는 안 돌린 단계의 기록을 `fromRun`을 달아
이어받게 고쳤습니다(2026-07-29).

**erd는 문법 검증을 통과하지 못한 채 저장됐습니다.** 무한히 다시 묻느니 못 통과했다고
적는 편이 낫다는 판단이고, 그 사실이 여기와 `design/RUN.json`에 남습니다. 배포
어댑터는 이 파일에서 엔티티를 읽어 냅니다 — 즉 **아래 결과는 문법이 성한 ERD에서
나온 것이 아닙니다.**

## 재현

요구사항·설계 산출물은 파일로 있으므로 **LLM 없이** 배포 계획까지 다시 만들어집니다.

```bash
python -m app.deployment.tools.intake_report \
    app/deployment/appkb/samples/lecture-platform --plan
```

씨앗 문장에서부터 다시 만들려면 `build_sample` → `build_design` 순입니다. 그때는
**같은 것이 나오지 않습니다** — `gpt-oss-120b`는 MoE라 `temperature=0`으로도
결정론이 아닙니다.

## 2026-07-29 시점의 결과 (커밋 `cc7bce7` + 미커밋 도구)

`intake_report`가 실제로 낸 것입니다. 값이지 주장이 아니라, 코드가 바뀌면 바뀝니다.

- 어댑터: **컴포넌트 1개** · 외부 0개 · 산출물 4종
- 신호: `has_api` · `needs_secret` · `uploads` · `owners` · `exposed` · `sync_calls`
  **있음**, `any_async` **없음**
- RESOURCE_SPEC: 계약 통과(`provider` · `region` · `monthlyBudgetUSD` ·
  `expectedConcurrentUsers` 4칸)
- 계획: 노드 10 · 선 5

`README.md`가 이 표본으로 가리려던 셋 중 둘의 답이 여기 있습니다.

1. **컴포넌트 경계 — 픽스처 탓이 아니었습니다.** 실물 클래스 다이어그램(9,373B)에서도
   배포 단위를 가를 신호가 안 나와 앱 하나로 뭉칩니다.
2. **비동기 — 픽스처 탓이 아니었습니다.** 실물 시퀀스 다이어그램에 `->>`가 **0개**라
   큐가 서지 않습니다. 씨앗 문장 3번("업로드된 영상은 자동으로 변환된다")과
   14번("변환이 실패해도 나머지는 계속 동작")이 비동기를 요구하는데도 그렇습니다 —
   **끊긴 자리는 배포 어댑터가 아니라 그 앞**입니다.
3. `resource_spec` 충족률은 **통과**입니다. 되묻기 없는 배치 경로라 구조적으로 0일
   것을 의심했는데, 제약 원문이 따로 들어오면 채워집니다.
