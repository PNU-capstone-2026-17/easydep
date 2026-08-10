# 세 capability의 LLM 추출·벤더 projection 개발 측정

## 목적과 경계

근거가 확보된 `persistent-block-storage`, `load-balanced-ingress`,
`https-load-balanced-ingress`가 자연어 CNA 요구사항에서 추출되어 AWS·Azure·GCP의 런타임
projection으로 연결되는지 측정했다. 실제 클라우드 생성 가능성과 앱 기능 검증은 후속 종단
실험의 별도 gate이며, 이번 측정에서는 LLM capability 추출과 로컬 DepKB 조회만 실행했다.
Docker와 cloud `apply`는 사용하지 않았다.

명시적 금지를 일반 사용 사례로 가정하지 않는다. 최종 측정은 기존 treatment 요구사항의 두
번째 항목에서 이미 존재하는 긍정 문장만 선택했다. 새 요구 문장을 합성하지 않았으며 선택된
문장과 해시는 원시 결과에 보존했다.

## 설정

- 모델: `openai/gpt-oss-120b`
- temperature: `0.0`
- seed: `42`
- capability당 표본: 5개
- 최종 live 호출: 15회
- 최종 LLM·로컬 projection 전체 시간: 73.021초
- CSP projection 셀: 3 capability × 3 CSP = 9개

## 최종 긍정 capability 측정 결과

| capability | LLM 시간 | stable ID 추출 | AWS | Azure | GCP | 판정 |
|---|---:|---|---|---|---|---|
| 영속 블록 스토리지 | 21.311초 | 실패 | 실패 | 실패 | 실패 | 표현 변이 미해결 |
| HTTP 부하분산 ingress | 25.389초 | 통과 | 통과 | 통과 | 통과 | 개발 연결 확인 |
| HTTPS 부하분산 ingress | 26.262초 | 통과 | 통과 | 통과 | 통과 | 개발 연결 확인 |

capability가 없는 결정론적 기본 입력은 세 CSP 모두 `vm`만 선택하고 realization을 만들지 않아
통과했다.

영속성 실패에서 LLM은 `persistent_storage_notes`라는 need를 만들고 역할을 “VM 교체 뒤에도
존속하는 durable block storage”, metadata를 `size_gib=20`, `mount_path=/var/lib/notes`로
정확히 추출했다. 그러나 `dependencyCapabilityIds`를 비워 두었고 key도 stable ID의 단순
하이픈·밑줄 정규형과 달라 DepKB에 연결되지 않았다. 의미 추출 성공을 projection 성공으로
바꾸는 사례별 별칭은 추가하지 않았다.

HTTP 부하분산은 3사에서 각각 `http-alb`, `http-application-gateway`, `global-http-alb`를,
HTTPS 부하분산은 `https-alb`, `https-application-gateway`, `global-https-alb`를 선택했다.
함께 추출된 다중 영역·health routing need는 현재 별도 stable ID가 없어 coverage에
미모델링으로 남지만, 해당 ingress projection의 성공 판정과 섞지 않았다.

## 개발 과정에서 보존한 관찰

첫 live 실행은 모델이 세 stable 의미를 동적 key로 만들었지만 새 ID 필드를 채우지 않아
추출 0/3, projection 0/9였다. 그 출력을 현재 정규형 연결로 오프라인 재생하자 LLM 추가 호출
없이 3/3, 9/9가 됐다. 이후 전체 요구사항을 넣은 확인 실행은 실험용 금지 문장까지 영속성
축으로 인식해 exact 추출 2/3이었지만 필요한 projection은 9/9였다. 이 결과 때문에 최종
측정에서는 사용자가 지적한 대로 금지 문장과 무관한 앱 요구를 제외했다.

## 원시 산출물

- 최초 live: `artifacts/measurements/capability-projection-live-20260809.json`
  (`81897dc16345757d1f4d4a169552df2abe3192cbafaa5d6790fafc87a784e0bc`)
- 최초 결과의 오프라인 재생: `artifacts/measurements/capability-projection-replay-20260809.json`
  (`6a4b0851498fedd3d84b27f1b19758ff7c6244d47c145e7859abf4bdd396c9e3`)
- 전체 요구사항 확인 live: `artifacts/measurements/capability-projection-live-confirmation-20260809.json`
  (`e4413392aea2b0beae0a216fa3064dd5ab8d7f705aed9018a8f5f70617e519eb`)
- 최종 긍정 capability live: `artifacts/measurements/capability-projection-live-positive-20260809.json`
  (`aa84e7edd702df6847272241b1f95f9427377aa5baf37c17474580af55b8f3c6`)

## 해석

현재 결과는 세 capability 모델 전체의 성공이 아니다. 부하분산 두 축은 요구사항에서 벤더별
다대다 realization까지 연결됐고, 영속성은 LLM이 의미를 알아도 stable ID로 전달하지 못하는
경계 실패가 남았다. 다음 보완은 `persistent_storage_notes` 하나의 별칭이 아니라, 근거 span을
사용해 열린 need를 지원 capability에 제한적으로 분류하거나 질문으로 남기는 방법을 비교해야
한다. 그 방법을 고르기 전에는 실제 클라우드 종단 실험을 세 축 모두 성공했다고 해석하지 않는다.

## 과하지 않은 후속 연결 방법

실측 뒤에는 별도 분류 모델이나 임베딩 저장소를 추가하지 않고, 엔티티 연결의 “제한된 후보
생성 + 연결 불가(NIL)”와 selective classification의 거부 원칙만 적용했다. TOSCA의
requirement–capability 분리도 자유 요구 이름과 stable capability type을 구분하는 표현 근거로
참고했다.

- stable ID 자체를 토큰으로 나눠 key·role·근거 span에 모든 토큰이 존재할 때만 후보로 만든다.
- `https-load-balanced-ingress`처럼 한 후보가 다른 후보의 토큰을 엄격히 포함하면 더 구체적인
  후보 하나를 선택한다.
- 서로 포함되지 않는 후보가 여러 개이거나 후보가 없으면 연결하지 않는다.
- 반복 LLM 표본 전부가 같은 연결 결과를 낼 때만 stable ID를 하류에 전달한다.
- 동의어 사전, 사례 ID, 임베딩 임계값, 추가 LLM 호출은 사용하지 않는다.

근거 자료는 [Selective Classification](https://arxiv.org/abs/1705.08500)의 coverage–risk
교환과 거부 개념, [TOSCA 2.0](https://docs.oasis-open.org/tosca/TOSCA/v2.0/TOSCA-v2.0.html)의
명시적인 requirement·capability type 분리다. 이 연구들을 현재 세 ID의 정확도를 보장하는
근거로 사용하지 않고, 애매한 입력을 강제 연결하지 않는 설계 원칙에만 사용했다.

최종 positive live 원시 출력을 이 결정론적 연결기로 오프라인 재생하자 추가 LLM 호출 없이
추출 3/3, CSP projection 9/9가 됐다. 이는 새 독립 LLM 반복이 아니라 동일 출력에 대한 개발
보완 재생이므로 live 성공률과 합치지 않는다. 재생 산출물은
`artifacts/measurements/capability-projection-live-positive-replay-linked-20260809.json`
(`df80c8a144ef9a243e7b5555ac377e9bebbff63a3982866d9788df5f9dc42c25`)에 보존했다.

## 제한적 연결 적용 후 독립 확인

커밋 `5ae3ccc`의 연결 코드를 동결한 상태에서 새 LLM 출력을 한 번 측정했다. 세 capability에
각 5회씩 총 15회를 호출했고 추출 3/3, AWS·Azure·GCP projection 9/9가 통과했다. 셀별
소요시간은 영속 블록 스토리지 21.646초, HTTP 부하분산 24.478초, HTTPS 부하분산
30.397초이며 전체는 76.584초였다.

원시 산출물은
`artifacts/measurements/capability-projection-live-linked-independent-20260809.json`
(`4f021137a6307a478426ba053c850a5dc12ec68db482fbcf120f974ad4d8e4c3`)에 보존했다.
이는 앞선 출력의 재생이 아닌 독립 live 확인이지만, 세 개발 축을 한 번 측정한 결과이므로
모집단 일반화나 최종 비교실험의 성공률로 사용하지 않는다.

같은 명령을 샌드박스 네트워크에서 먼저 실행했을 때에는 15회 모두 연결 오류로 degraded되어
0/3·0/9가 나왔다. 직후 단발 probe도 0.729초에 `APIConnectionError`였고, 승인된 외부
네트워크의 동일 probe는 TTFT 1.331초, 전체 1.568초에 정상 완료됐다. 따라서 이 0/3 결과는
429 속도제한이나 모델 추론 실패가 아니라 실행 환경 검열로 표시하며 측정 결과에 합치지 않는다.
