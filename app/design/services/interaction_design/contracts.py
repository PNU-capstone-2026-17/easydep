"""이전 제안 계약 import를 위한 호환 모듈이다.

새 코드에서는 :mod:`proposals`를 사용한다.
"""

from app.design.services.interaction_design.proposals import (
    CallPlanProposal,
    ClassOperations,
    FeedbackScope,
    FragmentDataType,
    InventoryField,
    InventoryItem,
    InventoryProposal,
    InventoryRelationship,
    OperationFragment,
    OperationProposal,
    Proposal,
    ProposedCall,
)

__all__ = [
    "CallPlanProposal",
    "ClassOperations",
    "FeedbackScope",
    "FragmentDataType",
    "InventoryField",
    "InventoryItem",
    "InventoryProposal",
    "InventoryRelationship",
    "OperationFragment",
    "OperationProposal",
    "Proposal",
    "ProposedCall",
]
