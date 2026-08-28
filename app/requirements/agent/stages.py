"""기존 agent stage registry import를 canonical 경계로 연결한다."""

from app.requirements.stage_registry import (
    GROUPS,
    PIPELINE,
    PRECLASSIFIED_GROUP,
    Stage,
    batch_order,
    cascade_order,
    editable_keys,
    node_by_key,
    nodes_in,
)

__all__ = [
    "GROUPS",
    "PIPELINE",
    "PRECLASSIFIED_GROUP",
    "Stage",
    "batch_order",
    "cascade_order",
    "editable_keys",
    "node_by_key",
    "nodes_in",
]
