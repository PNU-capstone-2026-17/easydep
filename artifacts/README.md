# 실행 산출물

EasyDep과 모든 비교 기준선의 실행 결과를 `runs/<run-id>/`에 저장한다. 일반 실행과 평가
실행을 별도 디렉터리에 중복 저장하지 않고 `manifest.json`의 `purpose`로 구분한다.

run ID 형식은 다음과 같다.

```text
<system>-<variant>-<case>-<UTC timestamp>-<short id>
```

예시:

```text
easydep-full-p1-20260805T103000Z-a1b2c3
easydep-no-cloud-kb-p1-20260805T103500Z-d4e5f6
cot-standard-p1-20260805T104000Z-g7h8i9
metagpt-standard-p1-20260805T104500Z-j1k2l3
```

각 실행 디렉터리의 `manifest.json`에는 `runId`, `system`, `variant`, `caseId`, `purpose`,
`completedStages`가 공통으로 들어간다. 산출물은 Git에 포함하지 않는다.
