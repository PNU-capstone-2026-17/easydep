export type Stage = 'requirements' | 'design' | 'implementation' | 'testing';
export type CommandStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'AWAITING_INPUT'
  | 'COMPLETED'
  | 'FAILED'
  | 'INTERRUPTED';

export type WaitReason = 'review' | 'question' | 'repair' | 'external_wait';

export interface ActionOffer {
  action: string;
  label: string;
  payload: Record<string, unknown>;
  auto_selectable: boolean;
  description?: string;
}

export interface RevisionPlanTarget {
  ref?: string | null;
  kind?: string | null;
  element_id?: string | null;
  owner?: string | null;
  artifact_type?: string | null;
  artifact_version_id?: number | null;
  display_label?: string | null;
}

export interface RevisionPlanMetadata {
  revision_plan?: unknown;
  requested_targets?: RevisionPlanTarget[];
  authority_targets?: RevisionPlanTarget[];
  downstream_targets?: RevisionPlanTarget[];
  message?: string | null;
}

export interface RevisionExecution {
  changed_stages?: string[];
  touched_targets?: Record<string, string[]>;
  regenerated_targets?: Record<string, string[]>;
  stale_targets?: Record<string, string[]>;
  target_remap?: Record<string, string>;
}

export function hasPendingRevisionPlan(
  kind: WorkspaceEvent['kind'],
  metadata: RevisionPlanMetadata | Record<string, unknown> | null | undefined
): boolean {
  return kind === 'action_required' && Boolean(metadata?.revision_plan);
}

export function hasCompletedRevisionExecution(
  kind: WorkspaceEvent['kind'],
  metadata: Record<string, unknown> | null | undefined
): boolean {
  return (
    kind === 'status' &&
    String(metadata?.status ?? '').toUpperCase() === 'COMPLETED' &&
    Boolean(metadata?.revision_execution)
  );
}

export function revisionPlanTargetLabel(target: RevisionPlanTarget): string {
  const displayLabel = String(target.display_label ?? '').trim();
  return displayLabel || String(target.ref ?? '').trim();
}

export interface WorkspaceCommandResult extends Record<string, any> {
  wait_reason?: WaitReason;
  actions?: ActionOffer[];
}

export interface WorkspaceCommand {
  command_id: string;
  app_id: string;
  action: string;
  stage: Stage;
  status: CommandStatus;
  payload: Record<string, unknown>;
  result?: WorkspaceCommandResult | null;
  error?: string | null;
  created_at?: string | null;
}

export interface WorkspaceEvent {
  event_id: number;
  app_id: string;
  command_id?: string | null;
  stage: Stage;
  kind: 'message' | 'status' | 'question' | 'action_required' | 'error' | string;
  actor: 'user' | 'assistant' | 'system';
  text: string;
  metadata: Record<string, any>;
  created_at?: string | null;
}

export interface WorkspaceApp {
  app_id: string;
  title: string;
  current_stage: Stage;
  created_at: string;
  command?: Pick<WorkspaceCommand, 'command_id' | 'action' | 'stage' | 'status' | 'created_at'> | null;
}

export interface LlmTimingPage {
  event_id: number;
  total: number;
  offset: number;
  timings: Array<Record<string, any>>;
}

export interface ArtifactSummary {
  available: boolean;
  status?: string | null;
  validation?: {
    valid?: boolean | null;
    errors?: string[];
    findings?: string[];
    check_status?: string | null;
    repair_iters?: number;
    method_proposals?: Array<{
      id: string;
      class_name: string;
      method: string;
      reason: string;
      use_case_ids?: string[];
      step_ids?: string[];
    }>;
  };
}

export interface SequenceDiagramSummary {
  use_case_id: string;
  use_case_name: string;
}

export interface BlockingFinding {
  code: string;
  stage: string;
  target_ids: string[];
  message: string;
  severity: string;
  repairable: boolean;
}

export interface RepairState {
  status: 'ACTIVE' | 'WAITING_EXTERNAL' | 'STALLED' | 'NEEDS_INPUT' | 'COMPLETED' | string;
  attempt_count: number;
  accepted_count: number;
  recent_attempts: Array<Record<string, unknown>>;
  tried_strategies?: string[];
  rejected_candidate_digests?: string[];
  finding_digest?: string;
  stall_reason?: string;
}

export interface LiveDiagramPreview {
  command_id: string;
  stage: 'class_diagram';
  revision: number;
  phase: string;
  unit: string;
  completed: number;
  total: number;
  puml: string;
}

export interface WorkspaceSnapshot {
  app_id: string;
  current_stage: Stage;
  command?: WorkspaceCommand | null;
  events: WorkspaceEvent[];
  artifacts: Record<string, ArtifactSummary>;
  deployment_preferences?: DeploymentPreferences | null;
}

export type CloudProvider = 'aws' | 'azure' | 'gcp';

export interface CloudRegionOption {
  code: string;
  name: string;
  latitude: number | null;
  longitude: number | null;
  zones: string[];
}

export interface DeploymentTarget {
  id?: string;
  provider: CloudProvider;
  region: string;
  zones?: string[];
  status?: string;
  issueCount?: number;
}

export interface DeploymentPreferences {
  mode: 'alternatives';
  targets: DeploymentTarget[];
  monthly_budget_amount?: number | null;
  monthly_budget_currency?: string;
  resource_constraints_text?: string;
}

export interface ArtifactDocument {
  artifacts: Record<string, unknown>;
  validation: Record<string, ArtifactSummary['validation']>;
  artifact_status: Record<string, string>;
  artifact_metadata?: Record<
    string,
    {
      schemaVersion?: string;
      readOnly?: boolean;
      regeneration?: { required?: boolean; targetSchemaVersion?: string; reason?: string };
      status?: string;
      selection?: { status?: string; reason?: string };
      selectedTarget?: DeploymentTarget | null;
      targets?: DeploymentTarget[];
    }
  >;
}

export interface ComputeSizingCandidate {
  sku: string;
  vCPU: number;
  memoryGiB: number;
  hourlyComputeUSD: number;
  monthlyComputeUSD: number;
  replicaCount: number;
}

export interface ComputeSizingUnit {
  computeUnitId: string;
  status: 'completed' | 'needsInput';
  reason?: string;
  minimumReplicaCount: number;
  selectedReplicaCount: number;
  replicationSafety: 'singleton' | 'interchangeable' | 'unknown';
  minimumRequirements: { minVCpu: number | null; minMemoryGiB: number | null };
  candidates: ComputeSizingCandidate[];
}

export interface CapacityOverride {
  computeUnitId: string;
  minVCpu: number;
  minMemoryGiB: number;
}

export interface DeploymentSizingResponse {
  target: DeploymentTarget;
  structureDigest: string;
  capacityOverrides?: CapacityOverride[];
  guidance: {
    provider: CloudProvider;
    region: string;
    currency: 'USD';
    hoursPerMonth: number;
    priceRetrievedAt: string;
    scope: string;
    computeUnits: ComputeSizingUnit[];
  };
  selected: Array<{
    computeUnitId: string;
    sku: string;
    replicaCount: number;
    replicationConfirmed: boolean;
  }>;
}

export interface FileArtifactSnapshot {
  artifact_type: string;
  version_no: number;
  metadata: Record<string, unknown>;
  files: Array<{ path: string; sha256: string }>;
  created_at: string;
}

export interface CommandPayload {
  action: string;
  text?: string;
  context?: Record<string, unknown> | null;
  action_id?: string;
  job_id?: string;
  implementation_job_id?: string;
  repair_testing_job_id?: string;
  checkpoint_stage?: 'requirements' | 'design' | 'implementation';
  restart_stage?: Stage;
}

export interface LiveSourceFile {
  path: string;
  artifact_type: string;
  artifact_path: string;
  sha256: string;
  size: number;
  exists: boolean;
  status: 'available' | 'writing';
}

export interface LiveSourceSnapshot {
  job_id: string;
  run_id: string;
  status: string;
  revision: string;
  files: LiveSourceFile[];
}

export interface ArtifactTraceResponse {
  app_id: string;
  ref: string | null;
  refs: string[];
  unknown_source_refs: string[];
  sources: string[];
  consumers: string[];
  upstream: string[];
  downstream: string[];
  files: string[];
  evidence: string[];
  trace_scope?: string;
  source_snapshot?: Record<string, unknown> | null;
  testing?: Record<string, unknown> | null;
}
