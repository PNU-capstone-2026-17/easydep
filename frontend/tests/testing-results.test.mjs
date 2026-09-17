import assert from 'node:assert/strict';
import test from 'node:test';
import { projectTestingRun } from '../src/lib/testing-results.ts';
import { publicTestingStatus } from '../src/lib/testing-status.ts';

test('presents pending workflow states without coercing them to PASS', () => {
  assert.equal(publicTestingStatus('PENDING'), 'PENDING');
  assert.equal(publicTestingStatus('REUSED'), 'PENDING');
  assert.equal(publicTestingStatus('RUNNING'), 'RUNNING');
  assert.equal(publicTestingStatus('PASS'), 'PASS');
  assert.equal(publicTestingStatus('FAIL'), 'FAIL');
  assert.equal(publicTestingStatus('SKIPPED'), 'PASS');
  assert.equal(publicTestingStatus('unknown'), 'PENDING');
});

test('keeps plan rows pending while preserving the advertised total from checkpoint and SSE', () => {
  const saved = { workflow_id: 'workflow-UC7', use_case_id: 'UC7', use_case_name: 'Join waitlist', status: 'RUNNING', updated_at: '2026-09-12T01:00:00Z' };
  const current = command({ status: 'RUNNING', payload: { testing_checkpoint: { testing_progress: {
    plans: { 'workflow-UC7': saved }, workflow_counts: { total: 3, passed: 0 }
  } } } });
  const restored = projectTestingRun({ command: current });
  assert.equal(restored.plans[0].name, 'Join waitlist');
  assert.equal(restored.plans[0].status, 'PENDING');
  assert.equal(restored.planTotal, 3);
  const view = projectTestingRun({ command: current, events: [
    { command_id: 'testing-command', metadata: { ...saved, progress_event: 'testingProgressUpdated', phase: 'planning', scope: 'workflow', status: 'PASS', total_workflows: 3, updated_at: '2026-09-12T01:01:00Z' } },
    { command_id: 'old-command', metadata: { ...saved, progress_event: 'testingProgressUpdated', phase: 'planning', scope: 'workflow', status: 'FAIL', updated_at: '2026-09-12T01:02:00Z' } }
  ] });
  assert.equal(view.plans[0].status, 'PENDING');
  assert.equal(view.planTotal, 3);
  assert.equal(view.workflowCounts.passed, 0);
});

test('keeps the plan denominator fixed as incremental plan rows arrive', () => {
  const current = command({ status: 'RUNNING', payload: { testing_checkpoint: { testing_progress: {
    plan_total: 3,
    plans: { 'workflow-UC1': { workflow_id: 'workflow-UC1', status: 'PENDING' } }
  } } } });
  const view = projectTestingRun({ command: current, events: [{
    command_id: 'testing-command',
    metadata: {
      progress_event: 'testingProgressUpdated', phase: 'planning', scope: 'workflow',
      workflow_id: 'workflow-UC2', status: 'PENDING', total_workflows: 3
    }
  }] });

  assert.equal(view?.plans.length, 2);
  assert.equal(view?.planTotal, 3);
});

test('counts pending plan rows as prepared without treating them as passed tests', () => {
  const view = projectTestingRun({
    command: command({
      status: 'RUNNING',
      payload: { testing_checkpoint: { testing_progress: { plans: {
        'workflow-UC1': { workflow_id: 'workflow-UC1', status: 'PENDING' },
        'workflow-UC2': { workflow_id: 'workflow-UC2', status: 'PENDING' }
      } } } }
    })
  });

  assert.equal(view?.plans.length, 2);
  assert.deepEqual(view?.plans.map((plan) => plan.status), ['PENDING', 'PENDING']);
  assert.equal(view?.workflowCounts.passed, 0);
});

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
                            operationPath: '/orders',
                            method: 'POST',
                            parameters: [{ name: 'customerId', in: 'query', value: '$inputs.customerId' }],
                            successCriteria: [{ condition: '$statusCode == 201' }],
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
                  trivyScan: { gateStatus: 'PASS', commands: [['trivy', 'config', '.']], targets: ['Dockerfile'], checks: ['configuration scan'] },
                  deploymentPackage: { gateStatus: 'PASS', targets: ['docker-compose.yml'] }
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
  assert.equal(view?.workflows[0].steps[0].expectedStatus, '201');
  assert.deepEqual(view?.workflows[0].steps[0].inputLinks, ['customerId=$inputs.customerId']);
  assert.equal(view?.gates.find((item) => item.id === 'static')?.plannedChecks[0], 'configuration scan');
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

test('does not treat a blocked design repair as a Testing run', () => {
  const view = projectTestingRun({
    command: command({
      command_id: 'design-repair-command',
      action: 'delegate_repair',
      stage: 'design',
      status: 'AWAITING_INPUT',
      result: {
        kind: 'action_required',
        blocking_findings: [{ code: 'api.schema-references-exist', message: 'Missing schema.' }],
        requires_revision: true
      }
    })
  });
  assert.equal(view, null);
});

test('keeps the initial failure beside a successful repaired result', () => {
  const view = projectTestingRun({
    command: command({
      action: 'delegate_repair',
      stage: 'implementation',
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
            verification: { reports: { dynamicFunctional: { gateStatus: 'PASS', workflows: [] } } },
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

test('uses final workflow results over a matching pending candidate plan', () => {
  const view = projectTestingRun({
    command: command({
      status: 'COMPLETED',
      payload: { testing_checkpoint: { testing_progress: { workflow_counts: { total: 1, passed: 0, pending: 1 } } } },
      result: {
        job: { result: {
          gateStatus: 'PASS',
          verification: { reports: { dynamicFunctional: {
            gateStatus: 'PASS',
            candidatePlan: { workflows: [{ workflowId: 'workflow-UC1', 'x-easydep-trace': { useCaseIds: ['UC1'] }, steps: [{ stepId: 'list', operationId: 'listCourses' }] }] },
            workflows: [{ workflowId: 'UC1', useCaseIds: ['UC1'], operations: [{ operationId: 'listCourses', method: 'GET', path: '/courses', responses: [{ status: '200' }] }], workflow: { summary: 'List courses', steps: [{ stepId: 'list', operationId: 'listCourses' }] }, result: { gateStatus: 'PASS' } }]
          } } }
        } }
      }
    })
  });

  assert.equal(view?.workflows.length, 1);
  assert.equal(view?.workflows[0].status, 'PASS');
  assert.equal(view?.workflows[0].steps[0].method, 'GET');
  assert.equal(view?.workflows[0].steps[0].path, '/courses');
  assert.equal(view?.workflows[0].steps[0].expectedStatus, '200');
  assert.deepEqual(view?.workflowCounts, { total: 1, completed: 1, passed: 1, failed: 0, running: 0, pending: 0 });
});

test('keeps candidate workflows pending until dynamic testing has a final result', () => {
  const view = projectTestingRun({
    command: command({
      status: 'RUNNING',
      result: { gateStatus: 'RUNNING', verification: { reports: { dynamicFunctional: {
        gateStatus: 'RUNNING',
        candidatePlan: { workflows: [{ workflowId: 'workflow-UC1', steps: [{ stepId: 'list', operationId: 'listCourses' }] }] }
      } } } }
    })
  });

  assert.equal(view?.workflows[0].status, 'PENDING');
  assert.equal(view?.workflowCounts.completed, 0);
  assert.equal(view?.workflowCounts.passed, 0);
});

test('does not treat completed profile generation as workflow verification', () => {
  const view = projectTestingRun({
    command: command({
      status: 'RUNNING',
      payload: { testing_checkpoint: { testing_progress: { workflow_counts: { total: 2, passed: 2, completed: 2 } } } },
      result: { gateStatus: 'PASS', verification: { reports: { dynamicFunctional: {
        gateStatus: 'PASS',
        candidatePlan: { workflows: [
          { workflowId: 'workflow-UC1', steps: [{ stepId: 'list', operationId: 'listCourses' }] },
          { workflowId: 'workflow-UC2', steps: [{ stepId: 'read', operationId: 'readCourse' }] }
        ] }
      } } } }
    })
  });

  assert.equal(view?.gateStatus, 'PENDING');
  assert.deepEqual(view?.workflowCounts, { total: 2, completed: 0, passed: 0, failed: 0, running: 0, pending: 2 });
  assert.deepEqual(view?.workflows.map((workflow) => workflow.status), ['PENDING', 'PENDING']);
});

test('keeps a generated profile workflow with empty steps pending until verification evidence arrives', () => {
  const view = projectTestingRun({
    command: command({
      status: 'RUNNING',
      result: { verification: { reports: { dynamicFunctional: {
        gateStatus: 'RUNNING',
        candidatePlan: { workflows: [{ workflowId: 'workflow-UC1', steps: [{ stepId: 'list' }] }] },
        workflows: [{
          workflowId: 'workflow-UC1',
          workflow: { steps: [{ stepId: 'list' }] },
          result: { status: 'PASSED', gateStatus: 'PASS', steps: [] }
        }]
      } } } }
    })
  });

  assert.equal(view?.workflows[0].status, 'PENDING');
  assert.deepEqual(view?.workflowCounts, { total: 1, completed: 0, passed: 0, failed: 0, running: 0, pending: 1 });
});

test('counts terminal dynamic report workflows', () => {
  const view = projectTestingRun({
    command: command({
      status: 'COMPLETED',
      result: { verification: { reports: { dynamicFunctional: {
        gateStatus: 'PASS',
        candidatePlan: { workflows: [
          { workflowId: 'workflow-UC1', steps: [{ stepId: 'list', operationId: 'listCourses' }] },
          { workflowId: 'workflow-UC2', steps: [{ stepId: 'read', operationId: 'readCourse' }] }
        ] },
        workflows: [{
          workflowId: 'workflow-UC1',
          workflow: { summary: 'List courses', steps: [{ stepId: 'list', operationId: 'listCourses' }] },
          result: { gateStatus: 'PASS', steps: [{ stepId: 'list', statusCode: 200 }] }
        }]
      } } } }
    })
  });

  assert.equal(view?.gateStatus, 'PASS');
  assert.deepEqual(view?.workflows.map((workflow) => workflow.status), ['PASS', 'PASS']);
  assert.deepEqual(view?.workflowCounts, { total: 2, completed: 2, passed: 2, failed: 0, running: 0, pending: 0 });
});

test('folds live workflow progress over a candidate plan and increments verification counts', () => {
  const current = command({
    status: 'RUNNING',
    payload: { testing_checkpoint: { testing_progress: {
      updated_at: '2026-09-12T01:00:00Z',
      workflows: { 'execution-1': { workflow_id: 'execution-1', use_case_id: 'UC1', status: 'PENDING' } }
    } } },
    result: { verification: { reports: { dynamicFunctional: {
      gateStatus: 'RUNNING',
      candidatePlan: { workflows: [
        { workflowId: 'workflow-UC1', 'x-easydep-trace': { useCaseIds: ['UC1'] }, steps: [{ stepId: 'list', operationId: 'listCourses' }] },
        { workflowId: 'workflow-UC2', steps: [{ stepId: 'read', operationId: 'readCourse' }] }
      ] },
      workflows: [{
        workflowId: 'workflow-UC1',
        useCaseIds: ['UC1'],
        workflow: { steps: [{ stepId: 'list', operationId: 'listCourses' }] },
        result: { status: 'PASSED', gateStatus: 'PASS', steps: [{ stepId: 'list' }] }
      }]
    } } } }
  });
  const pending = projectTestingRun({ command: current });

  assert.deepEqual(pending?.workflows.map((workflow) => workflow.status), ['PENDING', 'PENDING']);
  assert.deepEqual(pending?.workflowCounts, { total: 2, completed: 0, passed: 0, failed: 0, running: 0, pending: 2 });

  const view = projectTestingRun({
    command: current,
    events: [{
      event_id: 11,
      command_id: 'testing-command',
      created_at: '2026-09-12T01:01:00Z',
      metadata: {
        progress_event: 'testingProgressUpdated', phase: 'dynamic', scope: 'workflow',
        workflow_id: 'execution-1', use_case_id: 'UC1', status: 'PASS', updated_at: '2026-09-12T01:01:00Z',
        progress_step_label: 'List courses'
      }
    }]
  });

  assert.deepEqual(view?.workflows.map((workflow) => workflow.status), ['PASS', 'PENDING']);
  assert.deepEqual(view?.workflowCounts, { total: 2, completed: 1, passed: 1, failed: 0, running: 0, pending: 1 });
});

test('keeps a terminal report workflow ahead of a conflicting live progress event', () => {
  const view = projectTestingRun({
    command: command({
      status: 'COMPLETED',
      result: { verification: { reports: { dynamicFunctional: {
        gateStatus: 'PASS',
        workflows: [{
          workflowId: 'workflow-UC1',
          workflow: { summary: 'List courses', steps: [{ stepId: 'list', operationId: 'listCourses' }] },
          result: { gateStatus: 'PASS', steps: [{ stepId: 'list', statusCode: 200 }] }
        }]
      } } } }
    }),
    events: [{
      event_id: 12,
      command_id: 'testing-command',
      metadata: {
        progress_event: 'testingProgressUpdated', phase: 'dynamic', scope: 'workflow',
        workflow_id: 'workflow-UC1', status: 'FAIL', progress_step_label: 'Stale failure'
      }
    }]
  });

  assert.equal(view?.workflows[0].status, 'PASS');
  assert.equal(view?.workflowCounts.passed, 1);
});
