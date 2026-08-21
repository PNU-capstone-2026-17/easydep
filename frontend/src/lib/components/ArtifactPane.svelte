<script lang="ts">
  import { Braces, CheckCircle2, Clock3, FileText, Image, Layers3, Maximize2, ShieldCheck, X } from '@lucide/svelte';
  import type { ArtifactDocument, FileArtifactSnapshot, SequenceDiagramSummary, WorkspaceEvent } from '$lib/types';
  import { getArtifactFile, getFileArtifactVersions, getSequenceDiagrams, getVersions } from '$lib/api';
  import { errorMessage } from '$lib/utils';
  import ArtifactVisualization from '$lib/components/ArtifactVisualization.svelte';
  import ArtifactNavigator from '$lib/components/ArtifactNavigator.svelte';
  import DraggableDiagramViewport from '$lib/components/DraggableDiagramViewport.svelte';
  import { Badge } from '$lib/components/ui/badge';
  import { artifactLabels, artifactPresent, diagramArtifactTypes, requirementsArtifactTypes } from '$lib/artifacts';

  let {
    appId,
    document,
    fileArtifacts,
    events,
    selected,
    onSelect,
    onClose
  }: {
    appId: string;
    document?: ArtifactDocument | null;
    fileArtifacts: Record<string, FileArtifactSnapshot>;
    events: WorkspaceEvent[];
    selected: string;
    onSelect: (stage: string) => void;
    onClose?: () => void;
  } = $props();
  let tab = $state<'artifact' | 'validation' | 'changes' | 'evidence'>('artifact');
  let diagramExpanded = $state(false);
  let deploymentView = $state<'runtime' | 'provisioning'>('runtime');
  let indexOpen = $state(false);
  let versions = $state<Array<Record<string, any>>>([]);
  let versionsError = $state('');
  let selectedFile = $state('');
  let fileContent = $state('');
  let fileError = $state('');
  let sequenceDiagrams = $state<SequenceDiagramSummary[]>([]);
  let sequenceError = $state('');
  let sequenceLoading = $state(false);
  let expandedSequence = $state<SequenceDiagramSummary | null>(null);
  let sequenceLoadVersion = 0;
  let previouslySelected = '';
  let content = $derived(document?.artifacts?.[selected]);
  let fileArtifact = $derived(fileArtifacts[selected]);
  let validation = $derived(document?.validation?.[selected]);

  let availableStages = $derived(
    Object.keys(document?.artifacts ?? {})
      .filter((key) => key in artifactLabels && artifactPresent(document?.artifacts?.[key]))
      .concat(Object.keys(fileArtifacts))
  );

  $effect(() => {
    const currentSelection = selected;
    if (currentSelection !== previouslySelected) {
      previouslySelected = currentSelection;
      tab = 'artifact';
      diagramExpanded = false;
      deploymentView = 'runtime';
      expandedSequence = null;
      indexOpen = false;
    }
  });

  $effect(() => {
    if (!appId || !selected) return;
    versions = [];
    versionsError = '';
    const loader = fileArtifacts[selected] ? getFileArtifactVersions : getVersions;
    loader(appId, selected)
      .then((result) => (versions = result.versions))
      .catch((error) => (versionsError = errorMessage(error)));
  });

  $effect(() => {
    const currentAppId = appId;
    const currentSelection = selected;
    const loadVersion = ++sequenceLoadVersion;
    sequenceDiagrams = [];
    sequenceError = '';
    sequenceLoading = false;

    if (currentSelection !== 'sequence_diagram' || !currentAppId) return;

    sequenceLoading = true;
    getSequenceDiagrams(currentAppId)
      .then((result) => {
        if (loadVersion !== sequenceLoadVersion) return;
        sequenceDiagrams = result.diagrams;
      })
      .catch((error) => {
        if (loadVersion !== sequenceLoadVersion) return;
        sequenceError = errorMessage(error);
      })
      .finally(() => {
        if (loadVersion === sequenceLoadVersion) sequenceLoading = false;
      });
  });

  $effect(() => {
    const snapshot = fileArtifacts[selected];
    if (!snapshot) {
      selectedFile = '';
      fileContent = '';
      return;
    }
    selectedFile = snapshot.files[0]?.path ?? '';
    void loadFile(selectedFile);
  });

  async function loadFile(path: string) {
    if (!path) return;
    selectedFile = path;
    fileError = '';
    try {
      fileContent = (await getArtifactFile(appId, selected, path)).content;
    } catch (error) {
      fileError = errorMessage(error);
      fileContent = '';
    }
  }

  function rawContent(value: unknown) {
    return typeof value === 'string' ? value : JSON.stringify(value ?? {}, null, 2);
  }

  function handleKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      diagramExpanded = false;
      indexOpen = false;
    }
  }

  function selectFromIndex(stage: string) {
    onSelect(stage);
    indexOpen = false;
  }

  function sequenceImageUrl(diagram: SequenceDiagramSummary, extension: 'png' | 'svg') {
    return `/api/apps/${encodeURIComponent(appId)}/stages/sequence_diagram/diagrams/${encodeURIComponent(diagram.use_case_id)}/image.${extension}`;
  }

  function expandDiagram(diagram: SequenceDiagramSummary | null = null) {
    expandedSequence = diagram;
    diagramExpanded = true;
  }

  function diagramImageUrl(stage: string) {
    const encodedAppId = encodeURIComponent(appId);
    if (stage === 'deployment_diagram') {
      return `/api/apps/${encodedAppId}/stages/deployment_diagram/views/${deploymentView}/image.svg`;
    }
    return `/api/apps/${encodedAppId}/stages/${encodeURIComponent(stage)}/image.svg`;
  }
</script>

<svelte:window onkeydown={handleKeydown} />

<aside class="artifact-pane relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden border-l border-[#deded7] bg-white">
  <header class="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-[#e4e4de] px-3">
      <div class="flex min-w-0 items-center gap-2">
        <h2 class="truncate text-sm font-semibold">{artifactLabels[selected] ?? 'Development artifact'}</h2>
        <span class="shrink-0 text-[10px] text-[#85877e]">{availableStages.length} available</span>
      </div>
      <div class="flex shrink-0 items-center gap-1">
        <button
          class="focus-ring flex size-8 items-center justify-center rounded-lg text-[#60635a] hover:bg-[#f1f2ed]"
          onclick={() => (indexOpen = !indexOpen)}
          aria-label="Browse artifact index"
          aria-expanded={indexOpen}
        >
          <Layers3 size={15} />
        </button>
        {#if onClose}
          <button class="focus-ring flex size-8 items-center justify-center rounded-lg text-[#60635a] hover:bg-[#f1f2ed]" onclick={onClose} aria-label="Close artifact details">
            <X size={15} />
          </button>
        {/if}
      </div>
  </header>

  {#if indexOpen}
    <button
      class="absolute inset-x-0 bottom-0 top-12 z-20 cursor-default bg-black/5"
      onclick={() => (indexOpen = false)}
      aria-label="Close artifact index"
    ></button>
    <div class="absolute left-3 right-3 top-12 z-30 overflow-hidden rounded-xl border border-[#dfe1da] bg-white shadow-[0_12px_32px_rgba(31,45,37,.16)]">
      <div class="flex items-center justify-between border-b border-[#ecece6] px-3 py-2">
        <strong class="text-xs">Artifact index</strong>
        <span class="text-[10px] text-[#85877e]">Select an output</span>
      </div>
      <ArtifactNavigator {document} {fileArtifacts} {selected} onSelect={selectFromIndex} />
    </div>
  {/if}

  <div class="flex shrink-0 border-b border-[#e6e6e0] px-2 pt-1" role="tablist">
    {#each [
      ['artifact', 'Artifact'],
      ['validation', 'Validation'],
      ['changes', 'Changes'],
      ['evidence', 'Evidence']
    ] as item}
      <button
        class="focus-ring flex-1 border-b-2 px-1 py-2.5 text-[11px] font-semibold {tab === item[0]
          ? 'border-[#1f5d45] text-[#1f5d45]'
          : 'border-transparent text-[#7c7e75]'}"
        onclick={() => (tab = item[0] as typeof tab)}
      >{item[1]}</button>
    {/each}
  </div>

  <div class="scrollbar-thin flex-1 overflow-auto bg-[#fbfbf8] p-4 md:p-5">
    <div class="mx-auto w-full max-w-6xl">
    {#if !content && !fileArtifact}
      <div class="mt-16 text-center text-[#898b83]">
        <FileText class="mx-auto mb-3" size={25} strokeWidth={1.5} />
        <p class="text-xs">This artifact has not been generated yet.</p>
      </div>
    {:else if tab === 'artifact'}
      {#if selected === 'deployment_diagram' && document?.artifact_metadata?.deployment_diagram?.readOnly}
        <div class="mb-4 rounded-xl border border-[#e3c98b] bg-[#fff8e7] p-3 text-xs leading-5 text-[#755b24]" role="status">
          <strong class="block">Legacy deployment plan is read-only</strong>
          <span>{document.artifact_metadata.deployment_diagram.regeneration?.reason ?? 'Regenerate this artifact as WorkloadGraph v2 before editing or IaC generation.'}</span>
        </div>
      {/if}
      {#if fileArtifact}
        <div class="mb-3">
          <select
            class="focus-ring h-9 w-full rounded-xl border border-[#dadbd4] bg-white px-3 text-xs"
            value={selectedFile}
            onchange={(event) => loadFile(event.currentTarget.value)}
            aria-label="Select file"
          >
            {#each fileArtifact.files as file}<option value={file.path}>{file.path}</option>{/each}
          </select>
        </div>
        {#if fileError}<p class="mb-2 text-xs text-[#9a4139]">{fileError}</p>{/if}
        <pre class="prose-json min-h-40 overflow-auto rounded-xl border border-[#e1e1db] bg-white p-3">{fileContent}</pre>
      {:else}
      {#if diagramArtifactTypes.has(selected)}
        <div class="mb-4 overflow-hidden rounded-xl border border-[#deded7] bg-white p-3">
          <div class="mb-2 flex items-center justify-between gap-2 text-[11px] font-semibold text-[#676960]">
            <span class="flex items-center gap-2"><Image size={13} /> Rendered view</span>
            <span class="flex items-center gap-1 font-normal text-[#85877e]"><Maximize2 size={12} /> Click to expand</span>
          </div>
          {#if selected === 'deployment_diagram'}
            <div class="mb-3 grid grid-cols-2 rounded-lg bg-[#f0f1ed] p-1" role="tablist" aria-label="Deployment diagram view">
              {#each [['runtime', 'Runtime placement'], ['provisioning', 'Creation dependencies']] as view}
                <button
                  class="focus-ring rounded-md px-2 py-1.5 text-[11px] font-semibold {deploymentView === view[0] ? 'bg-white text-[#285b43] shadow-sm' : 'text-[#74776f]'}"
                  role="tab"
                  aria-selected={deploymentView === view[0]}
                  onclick={() => (deploymentView = view[0] as typeof deploymentView)}
                >{view[1]}</button>
              {/each}
            </div>
          {/if}
          {#if selected === 'sequence_diagram'}
            {#if sequenceLoading}
              <p class="rounded-lg bg-[#f8f8f5] p-6 text-center text-xs text-[#85877e]">Loading per-use-case diagrams...</p>
            {:else if sequenceError}
              <p class="rounded-lg bg-[#f8f8f5] p-6 text-center text-xs text-[#9a4139]">{sequenceError}</p>
            {:else if sequenceDiagrams.length === 0}
              <p class="rounded-lg bg-[#f8f8f5] p-6 text-center text-xs text-[#85877e]">No use-case sequence diagrams are available.</p>
            {:else}
              <div class="sequence-diagram-gallery grid grid-cols-1 gap-3 xl:grid-cols-2">
                {#each sequenceDiagrams as diagram (diagram.use_case_id)}
                  <article class="sequence-diagram-card min-w-0 overflow-hidden rounded-lg border border-[#e1e1db] bg-white">
                    <h3 class="truncate border-b border-[#ecece7] bg-[#f8f8f5] px-3 py-2 text-xs font-semibold">
                      {diagram.use_case_name && diagram.use_case_name !== diagram.use_case_id
                        ? `${diagram.use_case_id} · ${diagram.use_case_name}`
                        : diagram.use_case_id}
                    </h3>
                    <button
                      class="focus-ring block w-full cursor-zoom-in p-2"
                      onclick={() => expandDiagram(diagram)}
                      aria-label={`Expand ${diagram.use_case_name || diagram.use_case_id}`}
                    >
                      <img
                        class="sequence-diagram-image max-h-[52vh] w-full object-contain"
                        src={sequenceImageUrl(diagram, 'png')}
                        alt={`${diagram.use_case_name || diagram.use_case_id} sequence diagram`}
                        loading="lazy"
                      />
                    </button>
                  </article>
                {/each}
              </div>
            {/if}
          {:else}
            <button class="focus-ring block w-full cursor-zoom-in rounded-lg bg-[#f8f8f5] p-2" onclick={() => expandDiagram()} aria-label={`Expand ${artifactLabels[selected]}`}>
              <img
                class="max-h-[68vh] w-full object-contain"
                src={diagramImageUrl(selected)}
                alt={artifactLabels[selected]}
              />
            </button>
          {/if}
        </div>
      {:else}
        <ArtifactVisualization stage={selected} value={content} />
      {/if}
      <details class="mt-4 rounded-xl border border-[#e1e1db] bg-white">
        <summary class="flex cursor-pointer items-center gap-2 px-3 py-2.5 text-[11px] font-semibold text-[#65675f]">
          <Braces size={13} /> Raw source
        </summary>
        <pre class="prose-json overflow-auto border-t border-[#ecece7] p-3">{rawContent(content)}</pre>
      </details>
      {/if}
    {:else if tab === 'validation'}
      <div class="rounded-xl border border-[#e0e1da] bg-white p-4">
        <div class="mb-4 flex items-center gap-2">
          <ShieldCheck size={17} class="text-[#2d7354]" />
          <strong class="text-sm">Artifact validation</strong>
        </div>
        <dl class="space-y-3 text-xs">
          <div class="flex justify-between"><dt>Syntax validation</dt><dd>{validation?.valid == null ? 'Not applicable' : validation.valid ? 'Passed' : 'Failed'}</dd></div>
          <div class="flex justify-between"><dt>Rule-check status</dt><dd>{validation?.check_status ?? 'Not run'}</dd></div>
          <div class="flex justify-between"><dt>Automatic repair attempts</dt><dd>{validation?.repair_iters ?? 0}</dd></div>
        </dl>
        {#if validation?.errors?.length || validation?.findings?.length}
          <ul class="mt-4 space-y-2 border-t border-[#ecece7] pt-3 text-xs text-[#8b3d36]">
            {#each [...(validation.errors ?? []), ...(validation.findings ?? [])] as finding}
              <li>• {finding}</li>
            {/each}
          </ul>
        {:else}
          <p class="mt-4 flex items-center gap-2 border-t border-[#ecece7] pt-3 text-xs text-[#347154]">
            <CheckCircle2 size={14} /> No recorded errors.
          </p>
        {/if}
      </div>
    {:else if tab === 'changes'}
      <div class="space-y-2">
        {#if versionsError}<p class="text-xs text-[#9a4139]">{versionsError}</p>{/if}
        {#each versions as version}
          <div class="rounded-xl border border-[#e1e1db] bg-white p-3 text-xs">
            <div class="flex items-center justify-between">
              <strong>Version {version.version_no ?? version.versionNo}</strong>
              <Badge tone={version.syntax_valid === false ? 'danger' : 'neutral'}>{version.origin ?? 'generated'}</Badge>
            </div>
            <p class="mt-2 text-[11px] text-[#85877e]">{version.created_at ?? ''}</p>
          </div>
        {:else}
          <p class="mt-12 text-center text-xs text-[#898b83]">No version history is available.</p>
        {/each}
      </div>
    {:else}
      <div class="space-y-2">
        {#each events.filter((event) => event.stage === (fileArtifact ? 'implementation' : requirementsArtifactTypes.has(selected) ? 'requirements' : 'design')).slice().reverse() as event}
          <div class="rounded-xl border border-[#e1e1db] bg-white p-3">
            <div class="flex items-center gap-2 text-[11px] font-semibold"><Clock3 size={12} />{event.kind}</div>
            <p class="mt-2 text-xs leading-5 text-[#66685f]">{event.text}</p>
          </div>
        {:else}
          <p class="mt-12 text-center text-xs text-[#898b83]">No related execution evidence is available.</p>
        {/each}
      </div>
    {/if}
    </div>
  </div>
</aside>

{#if diagramExpanded && diagramArtifactTypes.has(selected)}
  <div
    class="fixed inset-0 z-50 bg-black/70 p-3 backdrop-blur-sm md:p-6"
    role="dialog"
    aria-modal="true"
    tabindex="-1"
    aria-label={`${artifactLabels[selected]} expanded view`}
    onkeydown={handleKeydown}
    onclick={(event) => {
      if (event.currentTarget === event.target) diagramExpanded = false;
    }}
  >
    <div class="flex h-full min-h-0 flex-col overflow-hidden rounded-2xl border border-white/20 bg-white shadow-2xl">
      <header class="flex shrink-0 items-center justify-between gap-4 border-b border-[#e2e2dc] px-4 py-3 md:px-6">
        <div class="min-w-0">
          <p class="text-[10px] font-bold uppercase tracking-[.14em] text-[#85877e]">Expanded diagram</p>
          <h2 class="truncate text-sm font-semibold">{artifactLabels[selected]}</h2>
        </div>
        <button class="focus-ring flex items-center gap-2 rounded-lg px-3 py-2 text-xs text-[#5f6259] hover:bg-[#f1f2ed]" onclick={() => (diagramExpanded = false)} aria-label="Close expanded diagram">
          <X size={16} /> Close
        </button>
      </header>
      <div class="min-h-0 flex-1 overflow-hidden">
        <DraggableDiagramViewport label={`${artifactLabels[selected]} movable diagram`}>
          {#if selected === 'sequence_diagram' && expandedSequence}
            <img
              class="mx-auto h-auto min-w-full max-w-none"
              src={sequenceImageUrl(expandedSequence, 'svg')}
              alt={`${expandedSequence.use_case_name || expandedSequence.use_case_id} expanded`}
              draggable="false"
            />
          {:else}
            <img
              class="mx-auto h-auto min-w-full max-w-none"
              src={diagramImageUrl(selected)}
              alt={`${artifactLabels[selected]} expanded`}
              draggable="false"
            />
          {/if}
        </DraggableDiagramViewport>
      </div>
    </div>
  </div>
{/if}
