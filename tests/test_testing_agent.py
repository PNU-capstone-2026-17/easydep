import os
import pytest
from unittest.mock import patch
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

@pytest.fixture
def temp_iac_dir(tmp_path):
    d = tmp_path / "terraform"
    d.mkdir()
    
    # Valid Terraform
    valid_tf = """
resource "aws_security_group_rule" "allow_web" {
  type        = "ingress"
  from_port   = 443
  to_port     = 443
  protocol    = "tcp"
  cidr_blocks = ["10.0.0.0/8"]
}

resource "aws_db_instance" "default" {
  publicly_accessible = false
}
"""
    (d / "valid.tf").write_text(valid_tf, encoding="utf-8")

    # Invalid Terraform
    invalid_tf = """
resource "aws_security_group_rule" "allow_all" {
  type        = "ingress"
  from_port   = 22
  to_port     = 22
  protocol    = "tcp"
  cidr_blocks = ["0.0.0.0/0"]
}

provider "aws" {
  access_key = "AKIAIOSFODNN7EXAMPLE"
  secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
}

resource "aws_db_instance" "bad_db" {
  publicly_accessible = true
}
"""
    (d / "invalid.tf").write_text(invalid_tf, encoding="utf-8")
    
    return str(d)

def test_static_verification_node(temp_manifests_dir, temp_iac_dir):
    graph = create_testing_graph()
    
    initial_state = {
        "run_id": "test-123",
        "manifests_dir": temp_manifests_dir,
        "iac_dir": temp_iac_dir,
        "errors": []
    }
    
    # We mock run_trivy_scan so we don't need a real Docker container during tests.
    with patch('app.testing.nodes.static_verification.run_trivy_scan') as mock_k8s_trivy, \
         patch('app.testing.nodes.iac_verification.run_trivy_scan') as mock_iac_trivy:
         
        # Simulate Trivy findings for K8s
        mock_k8s_trivy.return_value = [
            "[invalid.yaml] missing recommended label 'app.kubernetes.io/name' (HIGH): ...",
            "[invalid.yaml] missing 'resources' block (CRITICAL): ...",
            "[invalid.yaml] missing 'livenessProbe' (HIGH): ..."
        ]
        
        # Simulate Trivy findings for IaC
        mock_iac_trivy.return_value = [
            "[invalid.tf] allows ingress from 0.0.0.0/0 (CRITICAL): ...",
            "[invalid.tf] Hardcoded 'access_key' detected (CRITICAL): ...",
            "[invalid.tf] Hardcoded 'secret_key' detected (CRITICAL): ...",
            "[invalid.tf] publicly accessible (HIGH): ..."
        ]
        
        # Run the graph
        result = graph.invoke(initial_state)
    
    # Assertions
    assert "static_report" in result
    static_report = result["static_report"]
    
    assert static_report["status"] == "FAILED"
    
    issues = static_report["issues"]
    assert any("missing recommended label 'app.kubernetes.io/name'" in msg for msg in issues if "invalid" in msg)
    assert any("missing 'resources' block" in msg for msg in issues if "invalid" in msg)
    assert any("missing 'livenessProbe'" in msg for msg in issues if "invalid" in msg)
    
    # Check IaC report
    assert "iac_report" in result
    iac_report = result["iac_report"]
    assert iac_report["status"] == "FAILED"
    
    iac_issues = iac_report["issues"]
    assert any("allows ingress from 0.0.0.0/0" in msg for msg in iac_issues)
    assert any("Hardcoded 'access_key' detected" in msg for msg in iac_issues)
    assert any("Hardcoded 'secret_key' detected" in msg for msg in iac_issues)
    assert any("publicly accessible" in msg for msg in iac_issues)
    
    # Ensure it skipped placeholders properly
    assert "dynamic_functional_report" in result
    assert result["dynamic_functional_report"]["status"] == "SKIPPED"
