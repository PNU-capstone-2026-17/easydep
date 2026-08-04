# 비교실험 베이스라인

EasyDep 본체와 격리된 두 기준선이다. 두 방식은 같은 케이스 JSON과 같은 LLM 설정을
받고, 웹 검색과 EasyDep KB를 사용하지 않는다. 결과는 Git에서 제외되는
`artifacts/baselines/<method>/<case>/<run>`에 보존한다.

## 기준선

- `cot`: 단일 LLM 호출. 요구사항→설계→구현→테스트 순서로 점검하도록 지시한다.
- `metagpt`: MetaGPT 0.8.2의 기본 Software Company SOP. Python 3.11 Docker 이미지로 격리한다.

CoT의 비공개 추론문은 저장하거나 평가하지 않는다. 출력에는 재현 가능한 짧은 결정 근거만 남긴다.

## 준비

```powershell
$env:API_KEY="<NVIDIA NIM API key>"
$env:BASE_URL="https://integrate.api.nvidia.com/v1"
$env:MODEL="openai/gpt-oss-120b"
$env:BASELINE_TEMPERATURE="0"
$env:BASELINE_SEED="42"

docker build -f experiments/baselines/Dockerfile.metagpt -t easydep-metagpt:0.8.2 .
```

Windows에서는 위 빌드 전에 Docker Desktop의 Linux container engine이 실행 중이어야 한다.

MetaGPT는 공식 지원 범위가 Python 3.9 이상 3.12 미만이므로 프로젝트의 Python 3.13 환경에
설치하지 않는다. 컨테이너 안에는 MetaGPT 0.8.2를 고정한다.

## 실행

먼저 API를 호출하지 않는 검증을 수행한다.

```powershell
python -m experiments.baselines.cot experiments/baselines/cases/p1-stateless-detailed.json --dry-run
python -m experiments.baselines.metagpt experiments/baselines/cases/p1-stateless-detailed.json --dry-run
```

실제 실행:

```powershell
python -m experiments.baselines.cot experiments/baselines/cases/p1-stateless-detailed.json
python -m experiments.baselines.metagpt experiments/baselines/cases/p1-stateless-detailed.json
```

MetaGPT의 `--investment`와 `--rounds`는 모든 케이스에서 같은 값으로 고정한다. 파일럿 후 값을
바꾸면 기존 실행과 섞지 않고 실험 계획에도 새 값을 기록한다.

## 케이스 계약

각 JSON은 `caseId`, `requirements`, `cloudConstraints`, `scope`를 가져야 한다. 세부형·일반형·
불완전형은 문구만 다르게 하고 같은 프로필의 골드 기준은 공유한다. 각 실행의 `manifest.json`에는
모델, 온도, seed, Git revision, 시간과 실행 명령이 기록된다.

MetaGPT는 공식 기본 산출물 구조를 유지하므로 CoT와 파일명이 같을 필요는 없다. 후속 평가기는
각 저장소에서 리소스·의존관계·빌드·Docker·테스트 결과를 같은 규칙으로 추출해 비교한다.
