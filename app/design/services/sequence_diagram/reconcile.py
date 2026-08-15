"""제거된 시퀀스→클래스 자동 변경 경로의 호환 진입점."""
from __future__ import annotations

from app.design.schemas.architecture_state import ArchitectureState


def reconcile_class_methods(state: ArchitectureState) -> dict:
    """호환용 no-op.

    클래스 다이어그램은 시퀀스의 제약 입력이지 시퀀스 출력에 맞춰 고칠 대상이 아니다.
    과거에는 없는 메시지 메서드를 여기서 클래스에 추가해 잘못된 호출을 정상으로
    세탁했다. 이제 소유권 위반은 ``sequence_message_methods``가 검출하고 시퀀스
    리바이저가 기존 수신 메서드 중 하나로 다시 매핑한다.
    """
    return {}
