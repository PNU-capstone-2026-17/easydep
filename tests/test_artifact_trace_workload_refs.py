from app.artifact_trace import TraceRef
from app.artifact_trace_projection import project_artifact_trace


def _project(workload, resource):
    return project_artifact_trace(
        {
            "deployment_diagram_bundle": {
                "workloadGraph": {"workloads": [workload]},
                "projections": [
                    {
                        "provider": "aws",
                        "region": "ap-northeast-2",
                        "resourcePlan": {"nodes": [resource]},
                    }
                ],
            }
        }
    )


def test_shared_non_workload_source_ref_does_not_infer_workload_resource_edge():
    workload = TraceRef("workload", "orders-worker")
    resource = TraceRef("resource", "aws:ap-northeast-2:nodes:orders")
    trace = _project(
        {"id": "orders-worker", "name": "Orders worker", "sourceRefs": ["system:orders"]},
        {"id": "orders", "name": "Orders worker", "sourceRefs": ["system:orders"]},
    )

    assert resource not in trace.downstream(workload)


def test_explicit_canonical_workload_source_ref_creates_edge():
    workload = TraceRef("workload", "orders-worker")
    resource = TraceRef("resource", "aws:ap-northeast-2:nodes:orders")
    trace = _project(
        {"id": "orders-worker", "name": "Orders worker"},
        {"id": "orders", "sourceRefs": ["workload:orders-worker"]},
    )

    assert resource in trace.downstream(workload)


def test_explicit_workload_ref_creates_edge():
    workload = TraceRef("workload", "orders-worker")
    resource = TraceRef("resource", "aws:ap-northeast-2:nodes:orders")
    trace = _project(
        {"id": "orders-worker", "name": "Orders worker"},
        {"id": "orders", "workloadRef": "orders-worker"},
    )

    assert resource in trace.downstream(workload)
