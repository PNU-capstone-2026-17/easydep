# 중립 크로스워크 감사

범위: `neutral_candidates/crosswalk.json`을 고정된 Cloud-Barista, TOSCA, OCCI 후보 패킷 및 `validate_crosswalk`와만 비교했다. CSP 인벤토리/리뷰 또는 P1-P3 자료는 검토하지 않았다.

## 2차 결과

**문서화된 표현상의 한계 한 건을 전제로 통과.**

수정된 `validate_crosswalk`를 독립적으로 실행한 결과 통과했다. 독립적인 집합/개수 검사 결과도 다음과 같았다.

```text
source candidates: 41 expected, 41 mapped, 0 excluded
source relations:  94 expected, 94 classified
dispositions:      50 preserved, 44 structuralOnly, 0 excluded
by source:         Cloud-Barista 27, TOSCA 44, OCCI 23
```

모든 후보는 정확히 한 번 분류된다. 모든 관계 키 `(model, candidateId, relationIndex)`도 정확히 한 번 분류된다. 제외 항목이 0개라는 사실은 더 이상 커버리지 부풀리기의 증거가 아니다. TOSCA 후보는 소수의 외견상 인프라 개념으로 병합되지 않고, 명시적으로 언어에만 해당하는 `structuralSupport`로 일대일 유지된다.

## 이전 지적 사항

1. **통과 — `neutral.compute-group`:** "scaling unit"을 제거했다. 수정된 정의는 패킷이 뒷받침하는 그룹 크기, 노드 그룹화 및 로드 밸런서 백엔드의 집합적 역할로 한정된다. 크기 조정/스케일링은 명시적으로 미해결 상태로 남겼다.

2. **통과 — 서브넷/IPNetwork 분리:** `neutral.ip-network-segment`에는 이제 Cloud-Barista에서 식별되고 포함 관계가 확인된 서브넷 리소스만 들어간다. `neutral.ip-network-configuration`에는 기존 Network에 적용되는 OCCI의 조합 가능한 L3 mixin을 별도로 담았다. 종전의 잘못된 동일성/포함 관계 등치는 제거되었다.

3. **통과 — TOSCA 과도 병합 제거:** 이제 TOSCA 후보 15개 모두에 별도의 일대일 `neutral.tosca-*-structure` 개념이 있다. 각 정의는 패킷의 역할을 유지하며, 관계 44개 모두를 `structuralOnly`로 분류해 인프라 리소스라는 주장을 피했다.

4. **통과 — 관계 커버리지 추가:** 패킷 관계 94개 모두에 명시적인 커버리지 항목과 유효한 개념 ID가 있다. 대상 원천 용어가 추출된 후보로 해석되는 관계 93개에 대해 독립적으로 엔드포인트를 검사한 결과, 예상 원천/대상 개념 쌍과 `conceptIds` 사이의 불일치는 **0건**이었다.

## 의미 관계 표본

- Cloud-Barista `NodeInfo placedIn SubnetInfo`는 `neutral.compute`와 `neutral.ip-network-segment`로 매핑됨: **통과**.
- Cloud-Barista `NLBTargetGroupInfo targets NodeGroupInfo`는 `neutral.load-balancer-backend-group`과 `neutral.compute-group`으로 매핑됨: **통과**.
- OCCI `Network is applicable target of IPNetwork Mixin`은 `neutral.virtual-network`와 `neutral.ip-network-configuration`으로 매핑됨: **통과**.
- OCCI `IPNetwork Mixin applies to Network`는 같은 두 개념 사이의 역방향을 보존함: **통과**.
- OCCI `StorageLink must not select OS Template`은 `neutral.compute-storage-attachment`와 `neutral.machine-image`로 매핑됨: **통과**.
- TOSCA `requirement definition mayUse node filter definition`은 대응하는 두 일대일 구조 개념으로 매핑됨: **통과**.
- TOSCA `operation mayBeImplementedBy artifact`는 operation과 artifact 구조 개념으로 매핑됨: **통과**.

## 남은 한계

TOSCA `requirement-assignment`의 관계 인덱스 3(`creates -> relationship`)은 대상 용어 자체가 추출된 후보가 아닌 유일한 관계다. 따라서 해당 커버리지 항목에는 `neutral.tosca-requirement-assignment-structure`만 기재되어 있다. 이는 의미상 정직한 표현이며 승인에 지장을 주지 않지만, 두 엔드포인트를 갖춘 중립 그래프 엣지는 아니다. 다운스트림 코드에 커버리지 원장이 아닌 자체 완결형 그래프가 필요하다면 일반 relationship 구조 개념을 추가하거나 각 커버리지 항목에 대상 용어와 술어를 직접 유지해야 한다.

그 밖의 커버리지 레코드는 인덱싱된 원장으로 유지된다. 술어, 방향 및 대상은 관계 키를 사용해 불변 원천 패킷에서 복원한다. 소비자는 `conceptIds`의 순서만으로 관계의 술어나 방향을 판단해서는 안 된다.

## 1차 처분

1차 검토는 근거 없는 스케일링 의미론, 서브넷/mixin 과도 병합, TOSCA 버킷 과도 병합 및 관계 커버리지 부재 때문에 실패했다. 이 네 가지 차단 사항은 모두 2차 검토에서 해결되었다.
