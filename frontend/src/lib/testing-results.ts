import type { WorkspaceCommand, WorkspaceEvent } from '$lib/types';

export type TestingStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'PASS'
  | 'FAIL'
  | 'INCONCLUSIVE'
  | 'DEFERRED'
  | 'REUSED'
  | 'SKIPPED';

export interface TestingStepView {
  stepId: string;
  operationId?: string;
  label: string;
  status: TestingStatus;
  detail?: string;
  method?: string;
  path?: string;
  statusCode?: number;
  control?: string;
  elapsedMs?: number;
  attempt?: number;
  contractStatus?: string;
  semanticStatus?: string;
  criteria: Array<{ condition: string; passed?: boolean }>;
  request?: unknown;
  response?: unknown;
  cleanup?: boolean;
}

export interface TestingWorkflowView {
  workflowId: string;
  label: string;
  status: TestingStatus;
  detail?: string;
  steps: TestingStepView[];
  totalSteps?: number;
  requirementIds: string[];
  useCaseIds: string[];
  reused?: boolean;
}

export interface TestingGateView {
  id: string;
  label: string;
  status: TestingStatus;
  detail?: string;
  issues: string[];
  files: string[];
  commands: string[];
  elapsedMs?: number;
  reused?: boolean;
}

export interface TestingRunView {
  available: boolean;
  commandId?: string;
  targetImplementationJobId?: string;
  startedAt?: string;
  status: TestingStatus;
  gateStatus: TestingStatus;
  phase?: string;
  currentLabel?: string;
  currentDetail?: string;
  updatedAt?: string;
  blockingReason?: string;
  failedWorkflowId?: string;
  failedStepId?: string;
  candidateDigest?: string;
  gateCounts?: { passed: number; failed: number; inconclusive: number };
  workflowCounts: {
    total: number;
    completed?: number;
    passed: number;
    failed: number;
    running: number;
    pending: number;
  };
  workflows: TestingWorkflowView[];
  gates: TestingGateView[];
  findings: Array<{
    code: string;
    message: string;
    severity?: string;
    defectClass?: string;
    repairOwner?: string;
  }>;
  repair?: { status?: string; attemptCount?: number; acceptedCount?: number };
  initialFailure?: {
    gateStatus: TestingStatus;
    reason?: string;
    failedWorkflowId?: string;
    failedStepId?: string;
    findings: Array<{ code: string; message: string }>;
  };
  systemError?: string;
  arazzoDocument?: Record<string, unknown>;
  rawReport?: Record<string, unknown>;
}

const gateLabels: Record<string, string> = {
  dynamicFunctional: 'Dynamic API tests',
  static: 'Deployment security',
  package: 'Deployment package',
  iac: 'Infrastructure code'
};

const statusValues = new Set<TestingStatus>([
  'PENDING',
  'RUNNING',
  'PASS',
  'FAIL',
  'INCONCLUSIVE',
  'DEFERRED',
  'REUSED',
  'SKIPPED'
]);

function record(value: unknown): Record<string, any> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, any>)
    : {};
}

function status(value: unknown, fallback: TestingStatus = 'PENDING'): TestingStatus {
  const normalized = String(value ?? '').trim().toUpperCase();
  if (normalized === 'PASSED' || normalized === 'COMPLETED') return 'PASS';
  if (normalized === 'FAILED' || normalized === 'ERROR') return 'FAIL';
  if (normalized === 'UNAVAILABLE' || normalized === 'NOT_APPLICABLE') return 'INCONCLUSIVE';
  return statusValues.has(normalized as TestingStatus)
    ? (normalized as TestingStatus)
    : fallback;
}

function strings(value: unknown): string[] {
  const values = Array.isArray(value) ? value : value == null ? [] : [value];
  return values
    .map((item) => {
      if (typeof item === 'string') return item;
      const candidate = record(item);
      return String(candidate.message ?? candidate.path ?? candidate.command ?? '').trim();
    })
    .filter(Boolean);
}

function commandText(command: unknown): string {
  if (Array.isArray(command)) return command.map(String).join(' ');
  const value = record(command);
  if (Array.isArray(value.command)) return value.command.map(String).join(' ');
  return String(value.command ?? value.name ?? '').trim();
}

function gate(id: string, value: unknown, progressValue: unknown): TestingGateView {
  const item = record(value);
  const progress = record(progressValue);
  const commands = Array.isArray(item.commands)
    ? item.commands.map(commandText).filter(Boolean)
    : [];
  return {
    id,
    label: gateLabels[id] ?? id,
    status: status(
      item.reused ? 'REUSED' : item.deferred ? 'DEFERRED' : item.gateStatus ?? item.status ?? progress.status,
      'PENDING'
    ),
    detail:
      String(
        item.message ??
          item.reason ??
          progress.progress_detail ??
          progress.progress_step_label ??
          ''
      ).trim() || undefined,
    issues: strings(item.issues),
    files: strings(item.targets ?? item.files),
    commands,
    elapsedMs:
      typeof item.elapsedMs === 'number'
        ? item.elapsedMs
        : typeof progress.elapsed_ms === 'number'
          ? progress.elapsed_ms
          : undefined,
    reused: Boolean(item.reused)
  };
}

function criteria(value: unknown): Array<{ condition: string; passed?: boolean }> {
  return (Array.isArray(value) ? value : [])
    .map((raw) => {
      const item = record(raw);
      const criterion = record(item.criterion);
      return {
        condition: String(criterion.condition ?? item.condition ?? '').trim(),
        passed: typeof item.passed === 'boolean' ? item.passed : undefined
      };
    })
    .filter((item) => item.condition);
}

function reportStep(
  raw: unknown,
  planned: Record<string, any> = {},
  cleanup = false
): TestingStepView {
  const step = record(raw);
  const finding = record(step.finding);
  const code = typeof step.statusCode === 'number' ? step.statusCode : undefined;
  const failed = Boolean(finding.code) || step.contractStatus === 'FAIL' || step.semanticStatus === 'FAIL';
  return {
    stepId: String(step.stepId ?? planned.stepId ?? 'step'),
    label: String(step.summary ?? step.operationId ?? planned.operationId ?? step.stepId ?? planned.stepId ?? 'Step'),
    status: status(step.status ?? (failed ? 'FAIL' : code == null ? 'PENDING' : 'PASS')),
    detail: String(finding.message ?? '').trim() || undefined,
    operationId: String(step.operationId ?? planned.operationId ?? '').trim() || undefined,
    method: String(step.method ?? '').trim().toUpperCase() || undefined,
    path: String(step.path ?? '').trim() || undefined,
    statusCode: code,
    control: String(step.control ?? '').trim() || undefined,
    elapsedMs: typeof step.elapsedMs === 'number' ? step.elapsedMs : undefined,
    attempt: typeof step.attempt === 'number' ? step.attempt : undefined,
    contractStatus: String(step.contractStatus ?? '').trim() || undefined,
    semanticStatus: String(step.semanticStatus ?? '').trim() || undefined,
    criteria: criteria(step.criteria),
    request: step.request,
    response: step.responseBody,
    cleanup
  };
}

function workflowsFromProgress(progress: Record<string, any>): TestingWorkflowView[] {
  return Object.values(record(progress.workflows)).map((raw) => {
    const item = record(raw);
    const steps = Object.values(record(item.steps)).map((stepRaw) => {
      const step = record(stepRaw);
      return {
        stepId: String(step.step_id ?? step.stepId ?? 'step'),
        label: String(step.progress_step_label ?? step.label ?? step.step_id ?? step.stepId ?? 'Step'),
        status: status(step.status),
        detail: String(step.progress_detail ?? '').trim() || undefined,
        operationId: String(step.operation_id ?? step.operationId ?? '').trim() || undefined,
        method: String(step.method ?? '').trim().toUpperCase() || undefined,
        path: String(step.path ?? '').trim() || undefined,
        statusCode: typeof step.status_code === 'number' ? step.status_code : undefined,
        control: String(step.control ?? '').trim() || undefined,
        elapsedMs: typeof step.elapsed_ms === 'number' ? step.elapsed_ms : undefined,
        attempt: typeof step.attempt === 'number' ? step.attempt : undefined,
        contractStatus: String(step.contract_status ?? '').trim() || undefined,
        semanticStatus: String(step.semantic_status ?? '').trim() || undefined,
        criteria: []
      };
    });
    return {
      workflowId: String(item.workflow_id ?? item.workflowId ?? 'workflow'),
      label: String(item.progress_step_label ?? item.label ?? item.workflow_id ?? item.workflowId ?? 'Workflow'),
      status: status(item.status),
      detail: String(item.progress_detail ?? '').trim() || undefined,
      steps,
      totalSteps: typeof item.total_steps === 'number' ? item.total_steps : undefined,
      requirementIds: [],
      useCaseIds: [],
      reused: item.status === 'REUSED'
    };
  });
}

function workflowsFromReport(report: Record<string, any>): TestingWorkflowView[] {
  const results: TestingWorkflowView[] = (
    Array.isArray(report.workflows) ? report.workflows : []
  ).map((raw) => {
    const item = record(raw);
    const result = record(item.result);
    const workflow = record(item.workflow);
    const trace = record(workflow['x-easydep-trace'] ?? item['x-easydep-trace']);
    const executedSteps = Array.isArray(result.steps) ? result.steps : [];
    const executedIds = new Set(executedSteps.map((step) => String(record(step).stepId ?? '')));
    const planned = Array.isArray(workflow.steps) ? workflow.steps : [];
    let cleanupLane = false;
    const executedViews = executedSteps.map((step) => {
      const view = reportStep(step, {}, cleanupLane);
      if (String(record(step).control ?? '').startsWith('cleanup-')) cleanupLane = true;
      return view;
    });
    const unexecutedViews = planned
      .filter((plannedStep) => !executedIds.has(String(record(plannedStep).stepId ?? '')))
      .map((plannedStep) => {
        const definition = record(plannedStep);
        return reportStep(
          {
            ...definition,
            status: status(result.gateStatus ?? result.status) === 'PASS' ? 'SKIPPED' : 'PENDING'
          },
          definition
        );
      });
    const steps = [...executedViews, ...unexecutedViews];
    return {
      workflowId: String(item.workflowId ?? result.workflowId ?? workflow.workflowId ?? 'workflow'),
      label: String(workflow.summary ?? workflow.description ?? item.summary ?? item.workflowId ?? 'Workflow'),
      status: status(result.reused ? 'REUSED' : result.gateStatus ?? result.status),
      detail: String(result.reason ?? '').trim() || undefined,
      steps,
      totalSteps: planned.length || steps.length || undefined,
      requirementIds: strings(item.requirementIds ?? trace.requirementIds),
      useCaseIds: strings(item.useCaseIds ?? trace.useCaseIds),
      reused: Boolean(result.reused)
    };
  });
  const resultIds = new Set(results.map((item) => item.workflowId));
  const pendingIds = new Set(strings(report.pendingWorkflowIds));
  const plannedWorkflows = Array.isArray(record(report.candidatePlan).workflows)
    ? record(report.candidatePlan).workflows
    : [];
  for (const raw of plannedWorkflows) {
    const workflow = record(raw);
    const workflowId = String(workflow.workflowId ?? 'workflow');
    if (resultIds.has(workflowId)) continue;
    const trace = record(workflow['x-easydep-trace']);
    results.push({
      workflowId,
      label: String(workflow.summary ?? workflow.description ?? workflowId),
      status: pendingIds.has(workflowId) ? 'PENDING' : 'SKIPPED',
      steps: (Array.isArray(workflow.steps) ? workflow.steps : []).map((step) =>
        reportStep({ ...record(step), status: 'PENDING' }, record(step))
      ),
      totalSteps: Array.isArray(workflow.steps) ? workflow.steps.length : undefined,
      requirementIds: strings(trace.requirementIds),
      useCaseIds: strings(trace.useCaseIds)
    });
  }
  return results;
}

function finalReport(value: unknown): Record<string, any> {
  const root = record(value);
  if (root.verification || root.gateStatus || root.blocking_findings) return root;
  const jobReport = record(record(root.job).result);
  if (Object.keys(jobReport).length) return jobReport;
  const nested = record(root.result);
  return nested.verification || nested.gateStatus ? nested : {};
}

function latestProgressEvent(
  events: WorkspaceEvent[],
  commandId: string | undefined
): WorkspaceEvent | undefined {
  return [...events].reverse().find(
    (event) =>
      event.metadata?.progress_event === 'testingProgressUpdated' &&
      (!commandId || event.command_id === commandId)
  );
}

export function projectTestingRun(input: {
  command?: WorkspaceCommand | null;
  events?: WorkspaceEvent[];
  result?: Record<string, any> | null;
}): TestingRunView | null {
  const command = input.command ?? null;
  const outerResult = record(input.result ?? command?.result);
  const checkpoint = record(command?.payload?.testing_checkpoint);
  const checkpointResult = record(checkpoint.result);
  const report = finalReport(input.result ?? command?.result);
  const source = Object.keys(report).length ? report : checkpointResult;
  const verification = record(source.verification);
  const reports = record(verification.reports ?? source.reports);
  const event = latestProgressEvent(input.events ?? [], command?.command_id);
  const checkpointProgress = record(checkpoint.testing_progress);
  const progress = Object.keys(checkpointProgress).length
    ? checkpointProgress
    : record(event?.metadata);
  const lastProgress = record(progress.last_event);
  const currentProgress = Object.keys(lastProgress).length ? lastProgress : record(event?.metadata);
  const dynamicReport = record(reports.dynamicFunctional);
  const reportWorkflows = workflowsFromReport(dynamicReport);
  const progressWorkflows = workflowsFromProgress(progress);
  const workflows = reportWorkflows.length ? reportWorkflows : progressWorkflows;
  const progressGates = record(progress.gates);
  const aggregateGates = record(verification.gates ?? source.gates);
  const staticReport = record(reports.static);
  const gates = [
    gate(
      'dynamicFunctional',
      Object.keys(dynamicReport).length
        ? dynamicReport
        : { gateStatus: aggregateGates.dynamicFunctional },
      progressGates.dynamicFunctional
    ),
    gate(
      'static',
      Object.keys(record(staticReport.trivyScan)).length
        ? staticReport.trivyScan
        : { gateStatus: aggregateGates.static },
      progressGates.static
    ),
    gate('package', staticReport.deploymentPackage, progressGates.package),
    gate(
      'iac',
      Object.keys(record(reports.iac)).length
        ? reports.iac
        : { gateStatus: aggregateGates.iac },
      progressGates.iac
    )
  ];
  const blockers = Array.isArray(source.blocking_findings)
    ? source.blocking_findings
    : Array.isArray(outerResult.blocking_findings)
      ? outerResult.blocking_findings
      : [];
  const findings = blockers
    .map((raw) => {
      const item = record(raw);
      return {
        code: String(item.code ?? 'TESTING'),
        message: String(item.message ?? ''),
        severity: String(item.severity ?? '').trim() || undefined,
        defectClass: String(item.defect_class ?? '').trim() || undefined,
        repairOwner: String(item.repair_owner ?? '').trim() || undefined
      };
    })
    .filter((item) => item.message);
  const repairState = record(source.repair_state ?? outerResult.repair_state);
  const initialFailure = record(command?.payload?.initial_testing_failure);
  const hasTestingEvent = Boolean(event);
  const isTestingCommand =
    command?.stage === 'testing' ||
    command?.action === 'start_testing' ||
    command?.action === 'delegate_repair';
  if (!Object.keys(source).length && !Object.keys(progress).length && !hasTestingEvent && !isTestingCommand) {
    return null;
  }
  const counts = record(progress.workflow_counts);
  const gateCounts = record(verification.gateCounts ?? source.gateCounts);
  const finalGateStatus = status(source.gateStatus ?? verification.gateStatus, 'PENDING');
  const commandStatus = String(command?.status ?? '').toUpperCase();
  const runStatus: TestingStatus = Object.keys(report).length
    ? finalGateStatus
    : commandStatus === 'RUNNING' || commandStatus === 'QUEUED'
      ? 'RUNNING'
      : commandStatus === 'FAILED'
        ? 'FAIL'
        : status(currentProgress.status ?? currentProgress.progress_status);
  const blockingReason =
    String(source.blockingReason ?? verification.blockingReason ?? findings[0]?.message ?? '').trim() ||
    undefined;

  return {
    available: true,
    commandId: command?.command_id ?? event?.command_id ?? undefined,
    targetImplementationJobId:
      String(
        record(outerResult.job).implementation_job_id ??
          checkpoint.implementation_job_id ??
          command?.payload?.implementation_job_id ??
          ''
      ).trim() || undefined,
    startedAt: command?.created_at ?? undefined,
    status: runStatus,
    gateStatus: Object.keys(report).length ? finalGateStatus : runStatus,
    phase: String(progress.phase ?? currentProgress.phase ?? '').trim() || undefined,
    currentLabel: String(currentProgress.progress_step_label ?? '').trim() || undefined,
    currentDetail: String(currentProgress.progress_detail ?? '').trim() || undefined,
    updatedAt:
      String(progress.updated_at ?? currentProgress.updated_at ?? event?.created_at ?? '').trim() ||
      undefined,
    blockingReason,
    failedWorkflowId:
      String(dynamicReport.failedWorkflowId ?? source.failedWorkflowId ?? '').trim() || undefined,
    failedStepId: String(dynamicReport.failedStepId ?? source.failedStepId ?? '').trim() || undefined,
    candidateDigest:
      String(dynamicReport.candidateDigest ?? source.candidateDigest ?? '').trim() || undefined,
    gateCounts: Object.keys(gateCounts).length
      ? {
          passed: Number(gateCounts.passed ?? gateCounts.PASS ?? 0),
          failed: Number(gateCounts.failed ?? gateCounts.FAIL ?? 0),
          inconclusive: Number(
            gateCounts.inconclusive ?? gateCounts.INCONCLUSIVE ?? 0
          )
        }
      : undefined,
    workflowCounts: {
      total: Number(counts.total ?? workflows.length),
      completed: counts.completed == null ? undefined : Number(counts.completed),
      passed:
        counts.passed == null
          ? workflows.filter((item) => item.status === 'PASS' || item.status === 'REUSED').length
          : Number(counts.passed) + Number(counts.reused ?? 0),
      failed: Number(counts.failed ?? workflows.filter((item) => item.status === 'FAIL').length),
      running: Number(
        counts.running ?? workflows.filter((item) => item.status === 'RUNNING').length
      ),
      pending: Number(
        counts.pending ?? workflows.filter((item) => item.status === 'PENDING').length
      )
    },
    workflows,
    gates,
    findings,
    repair: Object.keys(repairState).length
      ? {
          status: String(repairState.status ?? ''),
          attemptCount: Number(repairState.attempt_count ?? 0),
          acceptedCount: Number(repairState.accepted_count ?? 0)
      }
      : undefined,
    initialFailure: Object.keys(initialFailure).length
      ? {
          gateStatus: status(initialFailure.gateStatus, 'FAIL'),
          reason: String(initialFailure.blockingReason ?? '').trim() || undefined,
          failedWorkflowId:
            String(initialFailure.failedWorkflowId ?? '').trim() || undefined,
          failedStepId: String(initialFailure.failedStepId ?? '').trim() || undefined,
          findings: (Array.isArray(initialFailure.blocking_findings)
            ? initialFailure.blocking_findings
            : []
          ).map((raw) => {
            const item = record(raw);
            return {
              code: String(item.code ?? 'TESTING'),
              message: String(item.message ?? '')
            };
          }).filter((item) => item.message)
        }
      : undefined,
    systemError: command?.error?.trim() || undefined,
    arazzoDocument: Object.keys(record(dynamicReport.candidatePlan)).length
      ? record(dynamicReport.candidatePlan)
      : undefined,
    rawReport: Object.keys(source).length ? source : undefined
  };
}
