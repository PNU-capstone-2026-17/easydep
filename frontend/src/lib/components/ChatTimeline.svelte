<script lang="ts">
  import { AlertTriangle, Bot, CheckCircle2, CircleHelp, LoaderCircle, UserRound } from '@lucide/svelte';
  import type { ArtifactDocument, CloudProvider, CloudRegionOption, DeploymentPreferences, FileArtifactSnapshot, WorkspaceEvent } from '$lib/types';
  import { formatTime } from '$lib/utils';
  import { Badge } from '$lib/components/ui/badge';
  import ArtifactConversationCard from '$lib/components/ArtifactConversationCard.svelte';
  import { artifactPresent, fileArtifactTypes } from '$lib/artifacts';
  import DeploymentPreferencesCard from '$lib/components/DeploymentPreferencesCard.svelte';

  let {
    appId,
    events,
    document,
    fileArtifacts,
    regions,
    showDeploymentPreferences = false,
    preferenceSaving = false,
    onDeploymentPreferencesSave,
    onArtifactSelect
  }: {
    appId: string;
    events: WorkspaceEvent[];
    document?: ArtifactDocument | null;
    fileArtifacts: Record<string, FileArtifactSnapshot>;
    regions: Record<CloudProvider, CloudRegionOption[]>;
    showDeploymentPreferences?: boolean;
    preferenceSaving?: boolean;
    onDeploymentPreferencesSave: (preferences: DeploymentPreferences) => Promise<void>;
    onArtifactSelect: (stage: string) => void;
  } = $props();
  let latestProgress = $derived([...events].reverse().find((event) => event.kind === 'progress'));
  let implementationFocus = $derived.by(() => {
    const metadata = latestProgress?.metadata ?? {};
    if (String(metadata.progress_event ?? '') !== 'implementationFileProgress') return null;
    const file = String(metadata.current_file ?? '');
    const className = String(metadata.current_class ?? '');
    if (!file && !className) return null;
    return { file, className };
  });
  let activeSpecTasks = $derived(
    Array.isArray(latestProgress?.metadata?.active_spec_tasks)
      ? latestProgress.metadata.active_spec_tasks
      : []
  );
  let progressSteps = $derived.by(() => {
    if (!latestProgress) return [];
    const commandEvents = events.filter(
      (event) => event.kind === 'progress' && event.command_id === latestProgress?.command_id
    );
    const steps = new Map<
      string,
      { id: string; label: string; detail: string; status: string }
    >();
    for (const event of commandEvents) {
      const id = String(event.metadata?.analysis_step ?? event.metadata?.step ?? '');
      if (!id) continue;
      const previous = steps.get(id);
      const progressEvent = String(event.metadata?.progress_event ?? '');
      const status = String(
        event.metadata?.progress_status ??
          (progressEvent === 'analysisStepFinished'
            ? event.metadata?.status ?? 'completed'
            : progressEvent === 'analysisStepStarted'
              ? 'running'
              : previous?.status ?? 'running')
      );
      steps.set(id, {
        id,
        label: String(event.metadata?.progress_step_label ?? previous?.label ?? id),
        detail: String(event.metadata?.progress_detail ?? previous?.detail ?? ''),
        status
      });
    }
    return [...steps.values()];
  });
  let visibleEvents = $derived.by(() => {
    const lastProgressId = latestProgress?.event_id;
    return events.filter(
      (event) =>
        (event.kind !== 'status' || eventArtifactStages(event).length > 0) &&
        (event.kind !== 'progress' || event.event_id === lastProgressId)
    );
  });
  let artifactEventOwners = $derived.by(() => {
    const owners = new Map<string, number>();
    for (const event of events) {
      for (const stage of artifactCandidates(event).filter(available)) {
        owners.set(stage, event.event_id);
      }
    }
    return owners;
  });

  function available(stage: string): boolean {
    return Boolean(fileArtifacts[stage]) || artifactPresent(document?.artifacts?.[stage]);
  }

  function artifactCandidates(event: WorkspaceEvent): string[] {
    const candidates: string[] = [];
    const phase = String(event.metadata?.phase ?? '');
    if (event.stage === 'requirements' && event.kind !== 'status') {
      const requirementStage = {
        requirements: 'refined_requirements',
        use_cases: 'usecase_spec',
        specs: 'usecase_spec',
        relationships: 'usecase_diagram',
        diagram: 'usecase_diagram'
      }[phase];
      if (requirementStage) candidates.push(requirementStage);
    }

    if (event.stage === 'design' && event.kind !== 'status') {
      const designStage = String(
        event.metadata?.design?.stage ?? event.metadata?.current_stage ?? ''
      );
      if (designStage) candidates.push(designStage);
    }

    if (
      event.stage === 'implementation' &&
      event.actor === 'assistant' &&
      String(event.metadata?.status ?? '') === 'COMPLETED'
    ) {
      candidates.push(...fileArtifactTypes);
    }

    return [...new Set(candidates)];
  }

  function eventArtifactStages(event: WorkspaceEvent): string[] {
    return artifactCandidates(event).filter(
      (stage) => available(stage) && artifactEventOwners.get(stage) === event.event_id
    );
  }
</script>

<div class="mx-auto w-full max-w-3xl px-5 pb-8 pt-6">
  {#if events.length === 0}
    <div class="mt-20 text-center text-[#74766e]">
      <Bot class="mx-auto mb-4" size={30} strokeWidth={1.5} />
      <p class="text-sm">Waiting for the first command.</p>
    </div>
  {/if}
  {#each visibleEvents as event (event.event_id)}
    {@const relatedArtifacts = eventArtifactStages(event)}
    {#if event.kind === 'progress'}
      <div
        class="mb-4 ml-11 rounded-xl border border-[#dfe3dc] bg-[#fafbf8] px-3 py-2.5 text-xs text-[#555950]"
        data-kind="progress"
      >
        <div class="mb-2 flex items-center justify-between gap-3">
          <span class="font-semibold text-[#343831]">
            {String(event.metadata?.progress_card_label ?? 'Requirements analysis')}
          </span>
          <time class="text-[10px] text-[#a0a29a]">{formatTime(event.created_at)}</time>
        </div>
        <div class="space-y-2">
          {#if implementationFocus}
            <div class="rounded-lg border border-[#dfe6dd] bg-[#f3f7f2] px-2.5 py-2 text-[10px] leading-5 text-[#3b453f]">
              <div class="font-medium text-[#2f3d33]">Current implementation target</div>
              <div class="mt-0.5 flex flex-wrap items-center gap-1.5">
                <span class="font-mono text-[9px] text-[#57615d]">{implementationFocus.file}</span>
                {#if implementationFocus.className}
                  <span class="rounded bg-[#dfeee2] px-1.5 py-0.5 text-[9px] font-semibold text-[#2d7354]">{implementationFocus.className}</span>
                {/if}
              </div>
            </div>
          {/if}
          {#if progressSteps.length === 0}
            <div class="flex items-center gap-2">
              <LoaderCircle size={13} class="shrink-0 animate-spin text-[#2d7354]" />
              <span>{event.text}</span>
            </div>
          {:else}
            {#each progressSteps as step (step.id)}
              <div class="flex items-start gap-2">
                {#if step.status === 'completed'}
                  <CheckCircle2 size={13} class="mt-0.5 shrink-0 text-[#5d806c]" />
                {:else if step.status === 'failed' || step.status === 'timeout' || step.status === 'needs_review'}
                  <AlertTriangle size={13} class="mt-0.5 shrink-0 text-[#a8433a]" />
                {:else}
                  <LoaderCircle size={13} class="mt-0.5 shrink-0 animate-spin text-[#2d7354]" />
                {/if}
                <div class="min-w-0">
                  <div class="leading-4">{step.label}</div>
                  {#if step.detail && step.detail !== 'Started'}
                    <div class="mt-0.5 text-[10px] leading-4 text-[#85887f]">{step.detail}</div>
                  {/if}
                  {#if step.id === 'generate_specs' && activeSpecTasks.length}
                    <ul class="mt-1.5 space-y-1 border-l border-[#dce3dd] pl-2.5 text-[10px] leading-4 text-[#62675f]">
                      {#each activeSpecTasks as task (task.id)}
                        <li class="flex items-start gap-1.5">
                          <LoaderCircle size={10} class="mt-0.5 shrink-0 animate-spin text-[#2d7354]" />
                          <span><span class="font-mono">{task.id}</span> · {task.name}</span>
                        </li>
                      {/each}
                    </ul>
                  {/if}
                </div>
              </div>
            {/each}
          {/if}
        </div>
      </div>
    {:else}
    <article class="mb-5 flex gap-3" data-kind={event.kind}>
      <div
        class="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full border border-[#dcddd6] bg-white text-[#5d6058]"
      >
        {#if event.actor === 'user'}
          <UserRound size={15} />
        {:else if event.kind === 'error'}
          <AlertTriangle size={15} class="text-[#a8433a]" />
        {:else if event.kind === 'question' || event.kind === 'action_required'}
          <CircleHelp size={15} class="text-[#7d5b13]" />
        {:else}
          <Bot size={15} />
        {/if}
      </div>
      <div class="min-w-0 flex-1">
        <div class="mb-1.5 flex items-center gap-2">
          <span class="text-xs font-semibold">{event.actor === 'user' ? 'You' : 'EasyDep'}</span>
          <Badge tone={event.kind === 'error' ? 'danger' : event.kind === 'action_required' ? 'warning' : 'neutral'}>
            {event.stage}
          </Badge>
          <time class="text-[10px] text-[#96988f]">{formatTime(event.created_at)}</time>
        </div>
        <div
          class="whitespace-pre-wrap rounded-2xl border px-4 py-3 text-sm leading-6 shadow-[0_1px_2px_rgba(0,0,0,.02)] {event.actor === 'user'
            ? 'border-[#d8e5dd] bg-[#edf5f0]'
            : event.kind === 'error'
              ? 'border-[#eccbc7] bg-[#fff7f6]'
              : 'border-[#e3e3dd] bg-white'}"
        >
          {event.text}
          {#if event.metadata?.resource_question?.why}
            <p class="mt-2 border-t border-[#ece8dc] pt-2 text-xs leading-5 text-[#777267]">
              {event.metadata.resource_question.why}
            </p>
          {/if}
        </div>
        {#if relatedArtifacts.length}
          <div class="mt-3 grid gap-2 sm:grid-cols-2" aria-label="Generated artifacts">
            {#each relatedArtifacts as stage}
              <ArtifactConversationCard
                {appId}
                {stage}
                value={document?.artifacts?.[stage]}
                fileArtifact={fileArtifacts[stage]}
                validation={document?.validation?.[stage]}
                onOpen={onArtifactSelect}
              />
            {/each}
          </div>
        {/if}
      </div>
    </article>
    {/if}
  {/each}
  {#if showDeploymentPreferences && Object.values(regions).some((items) => items.length)}
    <DeploymentPreferencesCard
      {regions}
      saving={preferenceSaving}
      onSave={onDeploymentPreferencesSave}
    />
  {/if}
</div>
