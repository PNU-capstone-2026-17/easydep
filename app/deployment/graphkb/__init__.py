"""graphkb: 멀티클라우드 리소스 타입 의존성 그래프 지식베이스.

배포된 인스턴스가 아닌 "클라우드가 제공하는 리소스 타입 간 의존성"을
공개 스키마(CB-Tumblebug swagger, AWS CloudFormation Registry)에서
정적으로 추출하여 그래프로 직렬화한다.
"""

from __future__ import annotations

__version__ = "0.1.0"
