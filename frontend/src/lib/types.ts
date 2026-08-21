export type Stage = 'requirements' | 'design' | 'implementation' | 'testing';
export type CommandStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'AWAITING_INPUT'
  | 'COMPLETED'
  | 'FAILED'
  | 'INTERRUPTED';

export interface WorkspaceCommand {
  command_id: string;
  app_id: string;
  action: string;
  stage: Stage;
  status: CommandStatus;
  payload: Record<string, unknown>;
  result?: Record<string, any> | null;
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
  command?: WorkspaceCommand | null;
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
  };
}

export interface SequenceDiagramSummary {
  use_case_id: string;
  use_case_name: string;
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
  provider: CloudProvider;
  region: string;
  zones?: string[];
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
    }
  >;
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
  request_id?: string;
  implementation_job_id?: string;
}
