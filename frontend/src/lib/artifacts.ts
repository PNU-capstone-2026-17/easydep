export const artifactLabels: Record<string, string> = {
  refined_requirements: 'Refined requirements',
  usecase_spec: 'Use-case model',
  usecase_diagram: 'Use-case diagram',
  class_diagram: 'Class diagram',
  sequence_diagram: 'Sequence diagram',
  api_spec: 'OpenAPI',
  erd: 'ERD',
  deployment_diagram: 'Deployment diagram',
  SOURCE_CODE: 'Backend source',
  FRONTEND_SOURCE_CODE: 'Frontend source',
  TEST_CODE: 'Test code',
  DEPLOYMENT_FILE: 'Docker and deployment files',
  IAC_CODE: 'Terraform IaC'
};

export const diagramArtifactTypes = new Set([
  'usecase_diagram',
  'class_diagram',
  'sequence_diagram',
  'erd',
  'deployment_diagram'
]);

export const requirementsArtifactTypes = new Set([
  'refined_requirements',
  'usecase_spec',
  'usecase_diagram'
]);

export const fileArtifactTypes = [
  'SOURCE_CODE',
  'FRONTEND_SOURCE_CODE',
  'TEST_CODE',
  'DEPLOYMENT_FILE',
  'IAC_CODE'
];

export const internalArtifactTypes = new Set([
  'capability_contract',
  'resource_intake',
  'resource_spec'
]);

export function artifactPresent(value: unknown): boolean {
  if (typeof value === 'string') return value.length > 0;
  return value != null && typeof value === 'object' && Object.keys(value).length > 0;
}
