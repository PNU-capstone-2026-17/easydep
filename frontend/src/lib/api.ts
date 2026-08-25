import type {
  ArtifactDocument,
  CommandPayload,
  FileArtifactSnapshot,
  WorkspaceApp,
  WorkspaceEvent,
  WorkspaceSnapshot,
  CloudProvider,
  CloudRegionOption,
  DeploymentPreferences,
  SequenceDiagramSummary
} from '$lib/types';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) }
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail?.message ?? body.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function listApps() {
  const data = await request<{ apps: WorkspaceApp[] }>('/api/workspace/apps');
  return data.apps;
}

export function createApp(input: {
  message: string;
}) {
  return request<{ app_id: string }>('/api/workspace/apps', {
    method: 'POST',
    body: JSON.stringify(input)
  });
}

export function getCloudOptions() {
  return request<{
    regions: Record<CloudProvider, CloudRegionOption[]>;
    currencies: string[];
  }>('/api/workspace/cloud-options');
}

export function saveDeploymentPreferences(appId: string, input: DeploymentPreferences) {
  return request<{ preferences: DeploymentPreferences }>(
    `/api/workspace/apps/${appId}/deployment-preferences`,
    { method: 'PUT', body: JSON.stringify(input) }
  );
}

export function getWorkspace(appId: string) {
  return request<WorkspaceSnapshot>(`/api/workspace/apps/${appId}`);
}

export function sendCommand(appId: string, payload: CommandPayload) {
  return request(`/api/workspace/apps/${appId}/commands`, {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

export function getArtifacts(appId: string) {
  return request<{ app_id: string } & ArtifactDocument>(`/api/apps/${appId}`);
}

export function getVersions(appId: string, stage: string) {
  return request<{ versions: Array<Record<string, any>> }>(
    `/api/apps/${appId}/stages/${stage}/versions`
  );
}

export function getSequenceDiagrams(appId: string) {
  return request<{ diagrams: SequenceDiagramSummary[] }>(
    `/api/apps/${encodeURIComponent(appId)}/stages/sequence_diagram/diagrams`
  );
}

export function getFileArtifact(appId: string, artifactType: string) {
  return request<FileArtifactSnapshot>(
    `/api/implementation/apps/${appId}/artifacts/${artifactType}`
  );
}

export function getFileArtifactVersions(appId: string, artifactType: string) {
  return request<{ versions: Array<Record<string, any>> }>(
    `/api/implementation/apps/${appId}/artifacts/${artifactType}/versions`
  );
}

export function getArtifactFile(appId: string, artifactType: string, path: string) {
  return request<{ path: string; content: string; sha256: string }>(
    `/api/implementation/apps/${appId}/artifacts/${artifactType}/files/${path
      .split('/')
      .map(encodeURIComponent)
      .join('/')}`
  );
}

export async function downloadImplementationArtifacts(appId: string) {
  const response = await fetch(`/api/implementation/apps/${encodeURIComponent(appId)}/download`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail?.message ?? body.detail ?? `Request failed (${response.status})`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `easydep-${appId}-implementation.zip`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function connectEvents(
  appId: string,
  after: number,
  onEvent: (event: WorkspaceEvent) => void,
  onError: () => void
) {
  const source = new EventSource(`/api/workspace/apps/${appId}/events?after=${after}`);
  source.addEventListener('workspace', (raw) => {
    onEvent(JSON.parse((raw as MessageEvent).data));
  });
  source.onerror = onError;
  return source;
}
