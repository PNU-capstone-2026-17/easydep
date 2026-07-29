# 입력 요구사항 — 온라인 강의 플랫폼

이 샘플 한 벌의 **씨앗**입니다. 여기서 요구사항 에이전트 → 설계 에이전트 →
배포 계획까지 실제로 흘려보내고, 각 단계 산출물을 이 디렉터리에 남깁니다.

**요구 문장은 영어입니다** — 시스템의 입출력 언어가 영어입니다(프롬프트가 전부
영어이고, 도구 출력·판정문도 영어로 넘어갔습니다). 이 문서의 설명은 만드는 사람이
읽는 면이라 한국어로 둡니다.

## 왜 이 문장들인가 — 작성 원칙 셋

**① 클라우드 관심사를 미리 답해 놓지 않는다.**
29개 관심사를 요구 문장에 다 심어 두면 관심사 축이 "전부 충족"이라 답하고, 그건
측정이 아니라 연출입니다. **실제 사용자가 안 쓰는 것은 안 씁니다** — 무상태 여부·
다중화 범위·복구 목표·암호화 의무·관측 항목·테넌시는 **일부러 비웠습니다.** 축이
그것들을 인계 항목으로 드러내는지가 이 실험의 관전 포인트입니다.

**② 클라우드 제약은 요구 산문에 섞지 않는다.**
실측상 provider·region·예산은 요구 문장에 **0건** 나옵니다(`step_resource.py`).
그래서 입력도 그렇게 나눕니다 — 아래 `Cloud constraints`는 별도 입력입니다.

**③ 배포 축이 실제로 갈리게 만든다.**
파일 업로드·비동기 변환·외부 결제·인증이 있어야 객체 스토리지·큐·외부 시스템·
시크릿 저장소 신호가 갈립니다. CRUD 하나짜리로는 배포 계획이 VM 한 대로 끝나서
아무것도 시험하지 못합니다.

---

## Functional requirements

1. A student can sign up and log in with an email address and a password.
2. An instructor can upload a video file for a lecture.
3. An uploaded video is automatically converted into the format used for playback.
4. A student can search lectures and view the details of one.
5. A student can pay for a lecture and enrol in it. Payment is handled through an
   external payment provider.
6. A student's playback position is stored so that the next visit resumes where they
   left off.
7. An instructor can see the number of enrolled students and the revenue for each of
   their lectures.
8. An administrator can take an inappropriate lecture out of public view.

## Non-functional requirements

9. Listing lectures must respond within 2 seconds.
10. In the first week of a semester the traffic is about ten times the usual level.
11. Card details are never stored by us; they are handed to the payment provider.
12. Student personal data must be kept inside the country.
13. New features must be released without taking the service down.
14. If video conversion fails, the rest of the service must keep working for students.

## Cloud constraints (separate input)

> Deploy on AWS in the Seoul region. The monthly budget is at most 500 USD, and we
> expect about 300 concurrent users in normal times.

---

## 일부러 안 쓴 것 — 관전 포인트

아래는 **사용자가 보통 안 쓰는 것**이라 뺐습니다. 관심사 축이 이것들을 인계
항목으로 드러내야 합니다. 못 드러내면 축의 결함이고, 드러내면 이 과제의 주장
("사용자가 쓰지 않은 요구사항을 드러낸다")이 실물로 증명됩니다.

| 안 쓴 것 | 걸려야 할 관심사 | 배포 단계에서 |
|---|---|---|
| 인스턴스에 상태를 두는가 | `cn.stateless-process` | 서버리스 적합 판정 |
| 여러 가용영역에 퍼뜨리나 | `cn.redundancy-target` | `multiZone` |
| 얼마나 잃어도 되나 / 언제까지 복구 | `cn.recovery-objective` | 다중화 범위 |
| 저장 데이터 암호화 의무 | `cn.encryption-obligation` | 저장소 속성 고지 |
| 운영이 무엇을 봐야 하나 | `cn.operational-signal` | (아키타입 없음 — 경계) |
| 관리형 서비스를 써도 되나 | `cn.managed-vs-self` | 노드 종류 |
| 공개 노출 범위 | `cn.network-exposure` | 로드밸런서·보안그룹 |

반대로 **쓴 것 중 걸려야 하는 것**: 10번(`cn.traffic-shape` → spiky) ·
12번(`cn.data-residency`) · 13번(`cn.release-continuity` — 경계 갈래) ·
14번(`cn.degradation-policy` — 경계 갈래).

> 경계 갈래 둘을 일부러 넣었습니다. **"배포 계획이 소비할 수 없다"고 분류한 것이
> 실제 입력에서 나왔을 때 리포트가 그렇게 말하는지**를 봐야 하기 때문입니다.

---

## 실행 기록

여기서부터는 실제로 돌린 결과를 남깁니다(무엇을 돌렸는지·언제·무엇이 나왔는지).
`PROVENANCE.md`가 그 자리입니다.
