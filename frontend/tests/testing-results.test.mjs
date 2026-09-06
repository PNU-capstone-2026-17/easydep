import assert from 'node:assert/strict';
import test from 'node:test';
import { projectTestingRun } from '../src/lib/testing-results.ts';

function command(overrides = {}) {
  return {
    command_id: 'testing-command',
    app_id: 'app-1',
    action: 'start_testing',
    stage: 'testing',
    status: 'COMPLETED',
    payload: {},
    ...overrides
  };
}

test('projects a real Workspace Testing result wrapper', () => {
  const view = projectTestingRun({
    command: command({
      result: {
        message: 'Testing completed.',
        job_id: 'testing-command',
        job: {
          implementation_job_id: 'implementation-1',
          result: {
            passed: false,
            gateStatus: 'FAIL',
            gateCounts: { passed: 2, failed: 1, inconclusive: 0 },
            blocking_findings: [
              { code: 'testing.dynamic-functional', message: 'A workflow failed.', defect_class: 'SUT_DEFECT' }
            ],
            verification: {
              reports: {
                dynamicFunctional: {
                  gateStatus: 'FAIL',
                  failedWorkflowId: 'workflow-1',
                  failedStepId: 'step-1',
                  candidateDigest: 'candidate-1',
                  candidatePlan: {
                    arazzo: '1.1.0',
                    workflows: [
                      { workflowId: 'workflow-1', steps: [{ stepId: 'step-1', operationId: 'createOrder' }] },
                      { workflowId: 'workflow-2', summary: 'Read order', steps: [{ stepId: 'step-2', operationId: 'getOrder' }] }
                    ]
                  },
                  pendingWorkflowIds: ['workflow-2'],
                  workflows: [
                    {
                      workflowId: 'workflow-1',
                      requirementIds: ['FR-1'],
                      useCaseIds: ['UC-1'],
                      workflow: {
                        summary: 'Create order',
                        steps: [{ stepId: 'step-1', operationId: 'createOrder' }]
                      },
                      result: {
                        gateStatus: 'FAIL',
                        steps: [
                          {
                            stepId: 'step-1',
                            operationId: 'createOrder',
                            method: 'POST',
                            path: '/orders',
                            statusCode: 500,
                            semanticStatus: 'FAIL',
                            finding: { code: 'HTTP_500', message: 'Request failed' }
                          }
                        ]
                      }
                    }
                  ]
                },
                static: {
                  trivyScan: { gateStatus: 'PASS' },
                  deploymentPackage: { gateStatus: 'PASS' }
                },
                iac: { gateStatus: 'PASS' }
              }
            }
          }
        }
      }
    })
  });

  assert.equal(view?.gateStatus, 'FAIL');
  assert.equal(view?.targetImplementationJobId, 'implementation-1');
  assert.equal(view?.failedWorkflowId, 'workflow-1');
  assert.equal(view?.workflows[0].label, 'Create order');
  assert.equal(view?.workflows[0].steps[0].status, 'FAIL');
  assert.equal(view?.workflows[1].status, 'PENDING');
  assert.equal(view?.gates.find((item) => item.id === 'iac')?.status, 'PASS');
  assert.equal(view?.findings[0].defectClass, 'SUT_DEFECT');
});

test('restores accumulated progress from the Testing checkpoint', () => {
  const view = projectTestingRun({
    command: command({
      status: 'RUNNING',
      payload: {
        testing_checkpoint: {
          implementation_job_id: 'implementation-1',
          testing_progress: {
            phase: 'dynamic',
            status: 'RUNNING',
            active_workflow_id: 'workflow-1',
            workflow_counts: { total: 2, running: 1, pending: 1 },
            workflows: {
              'workflow-1': {
                workflow_id: 'workflow-1',
                status: 'RUNNING',
                progress_step_label: 'Create order',
                steps: {
                  health: {
                    step_id: 'health',
                    status: 'RUNNING',
                    progress_step_label: 'GET /health'
                  }
                }
              }
            },
            last_event: {
              progress_event: 'testingProgressUpdated',
              phase: 'dynamic',
              scope: 'step',
              status: 'RUNNING',
              progress_step_label: 'GET /health'
            }
          }
        }
      }
    }),
    events: [
      {
        event_id: 1,
        app_id: 'app-1',
        command_id: 'older-command',
        stage: 'testing',
        kind: 'progress',
        actor: 'system',
        text: '',
        metadata: {
          progress_event: 'testingProgressUpdated',
          phase: 'summary',
          status: 'FAIL'
        }
      }
    ]
  });

  assert.equal(view?.status, 'RUNNING');
  assert.equal(view?.phase, 'dynamic');
  assert.equal(view?.currentLabel, 'GET /health');
  assert.equal(view?.workflowCounts.total, 2);
  assert.equal(view?.workflows[0].steps[0].stepId, 'health');
});

test('does not invent a Testing run from an unrelated command or stale event', () => {
  const view = projectTestingRun({
    command: command({ command_id: 'design-command', action: 'message', stage: 'design', status: 'COMPLETED' }),
    events: [
      {
        event_id: 1,
        app_id: 'app-1',
        command_id: 'testing-command',
        stage: 'testing',
        kind: 'progress',
        actor: 'system',
        text: '',
        metadata: { progress_event: 'testingProgressUpdated', phase: 'dynamic', status: 'RUNNING' }
      }
    ]
  });
  assert.equal(view, null);
});

test('keeps the initial failure beside a successful repaired result', () => {
  const view = projectTestingRun({
    command: command({
      action: 'delegate_repair',
      payload: {
        initial_testing_failure: {
          gateStatus: 'FAIL',
          blockingReason: 'POST /orders returned HTTP 500.',
          failedWorkflowId: 'workflow-order',
          failedStepId: 'create',
          blocking_findings: [
            { code: 'testing.dynamic-functional', message: 'POST /orders failed.' }
          ]
        }
      },
      result: {
        job: {
          implementation_job_id: 'implementation-2',
          result: {
            gateStatus: 'PASS',
            gateCounts: { passed: 3, failed: 0, inconclusive: 0 },
            verification: { reports: {} },
            repair_state: { status: 'COMPLETED', attempt_count: 1, accepted_count: 1 }
          }
        }
      }
    })
  });
  assert.equal(view?.gateStatus, 'PASS');
  assert.equal(view?.initialFailure?.failedStepId, 'create');
  assert.equal(view?.repair?.status, 'COMPLETED');
});
