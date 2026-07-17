"""지식베이스 패키지들이 공유하는 최소 유틸.

`graphkb`(리소스 의존성)와 `capacitykb`(리소스 용량·제약)는 서로 독립적인
지식 차원이라 코드를 섞지 않는다. 다만 **같은 공개 스키마 소스**
(CloudFormation zip, bicep types, KCC CRD 등)를 내려받으므로,
다운로드 캐시만 이 패키지를 통해 공유한다.
"""

from __future__ import annotations
