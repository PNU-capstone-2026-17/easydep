# RTM 배치 요약

- 데이터셋 11개 · 채점 결정론만

| 데이터셋 | 요구 | FR | 커버 | orphan | verified | 거짓주장 | 복합FR | NFR | ack | gap | unattach | orphanUC |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bank_of_anthos | 27 | 15 | 15 | 0 | — | — | — | 16 | 12 | 4 | 4 | 0 |
| iot_telemetry | 32 | 27 | 27 | 0 | — | — | — | 20 | 20 | 0 | 0 | 0 |
| note_taking | 7 | 5 | 5 | 0 | — | — | — | 2 | 2 | 0 | 0 | 0 |
| online_boutique | 32 | 30 | 30 | 0 | — | — | — | 14 | 12 | 2 | 2 | 0 |
| ride_hailing | 26 | 20 | 20 | 0 | — | — | — | 14 | 9 | 5 | 5 | 0 |
| shopping_mall | 7 | 6 | 6 | 0 | — | — | — | 2 | 1 | 1 | 1 | 0 |
| sock_shop | 26 | 21 | 21 | 0 | — | — | — | 6 | 5 | 1 | 1 | 0 |
| telehealth | 26 | 39 | 39 | 0 | — | — | — | 14 | 12 | 2 | 2 | 0 |
| toystore | 23 | 19 | 19 | 0 | — | — | — | 9 | 9 | 0 | 0 | 0 |
| train_ticket | 32 | 37 | 37 | 0 | — | — | — | 4 | 2 | 2 | 2 | 0 |
| video_streaming | 32 | 34 | 34 | 0 | — | — | — | 23 | 11 | 12 | 12 | 0 |

> verified/거짓주장은 semantic(judge)이 켜졌을 때만 채워진다. ack=NFR이 UC로 라우팅됨(attached/linked), gap=unattached(횡단 제약 후보).
