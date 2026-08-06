import os
import pytest
from pathlib import Path

from app.testing.graphs.testing_graph import create_testing_graph
from app.testing.schemas.testing_state import TestingState

@pytest.fixture
def temp_manifests_dir(tmp_path):
    d = tmp_path / "k8s"
    d.mkdir()
    
    # Valid Deployment
    valid_yaml = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: valid-app
  labels:
    app.kubernetes.io/name: valid-app
spec:
  template:
    spec:
      containers:
      - name: main
        resources:
          limits:
            cpu: "1"
            memory: "1Gi"
        livenessProbe:
          httpGet:
            path: /health
        readinessProbe:
          httpGet:
            path: /ready
"""
    (d / "valid.yaml").write_text(valid_yaml, encoding="utf-8")

    # Invalid Deployment (missing limits, missing labels)
    invalid_yaml = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: invalid-app
spec:
  template:
    spec:
      containers:
      - name: main
        # missing resources
        # missing livenessProbe/readinessProbe
"""
    (d / "invalid.yaml").write_text(invalid_yaml, encoding="utf-8")
    
    return str(d)

def test_static_verification_node(temp_manifests_dir):
    graph = create_testing_graph()
    
    initial_state = {
        "run_id": "test-123",
        "manifests_dir": temp_manifests_dir,
        "errors": []
    }
    
    # Run the graph
    result = graph.invoke(initial_state)
    
    # Assertions
    assert "static_report" in result
    static_report = result["static_report"]
    
    assert static_report["status"] == "FAILED"
    
    issues = static_report["issues"]
    assert any("missing recommended label 'app.kubernetes.io/name'" in msg for msg in issues if "invalid-app" in msg)
    assert any("missing 'resources' block" in msg for msg in issues if "invalid-app" in msg)
    assert any("missing 'livenessProbe'" in msg for msg in issues if "invalid-app" in msg)
    
    # Ensure it skipped placeholders properly
    assert "iac_report" in result
    assert result["iac_report"]["status"] == "SKIPPED"
    
    assert "dynamic_functional_report" in result
    assert result["dynamic_functional_report"]["status"] == "SKIPPED"
