"""Provider-native discovery for evidence-first dependency analysis.

각 CSP의 공식 자료에서 수집한 경계와 관계를 다른 CSP의 공통 리소스 계층으로
변환하지 않고 그대로 검토한다.
"""

from .model import validate_inventory

__all__ = ["validate_inventory"]
