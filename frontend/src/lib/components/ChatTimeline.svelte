<script lang="ts">
  import { AlertTriangle, Bot, CheckCircle2, CircleHelp, LoaderCircle, UserRound } from '@lucide/svelte';
  import type { ArtifactDocument, CloudProvider, CloudRegionOption, DeploymentPreferences, FileArtifactSnapshot, WorkspaceEvent } from '$lib/types';
  import { formatTime } from '$lib/utils';
  import { Badge } from '$lib/components/ui/badge';
  import ArtifactConversationCard from '$lib/components/ArtifactConversationCard.svelte';
  import { artifactPresent, fileArtifactTypes } from '$lib/artifacts';
  import DeploymentPreferencesCard from '$lib/components/DeploymentPreferencesCard.svelte';
  import ImplementationErrorPanel from '$lib/components/ImplementationErrorPanel.svelte';

  let {
    appId,
    events,
    document,
    fileArtifacts,
    implementationErrors = [],
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
    implementationErrors?: string[];
    regions: Record<CloudProvider, CloudRegionOption[]>;
    showDeploymentPreferences?: boolean;
    preferenceSaving?: boolean;
    onDeploymentPreferencesSave: (preferences: DeploymentPreferences) => Promise<void>;
    onArtifactSelect: (stage: string) => void;
  } = $props();
  let latestProgress = $derived([...events].reverse().find((event) => event.kind === 'progress'));
  let latestImplementationError = $derived(
    [...events].reverse().find(
      (event) => event.stage === 'implementation' && event.kind === 'error'
    )?.event_id
  );
  let implementationCompletionEventId = $derived(
    [...events]
      .reverse()
      .find(
        (event) =>
          event.stage === 'implementation' &&
          event.kind === 'status' &&
          String(event.metadata?.status ?? '') === 'COMPLETED'
      )?.event_id ?? 0
  );
  let implementationTimelineResetId = $derived(
    [...events]
      .reverse()
      .find(
        (event) =>
          event.stage === 'implementation' &&
          event.metadata?.reset_implementation_timeline === true
      )?.event_id ?? 0
  );
  let activeSpecTasks = $derived(
    Array.isArray(latestProgress?.metadata?.active_spec_tasks)
      ? latestProgress.metadata.active_spec_tasks
      : []
  );
  let progressSteps = $derived.by(() => {
    if (!latestProgress) return [];
    const commandEvents = events.filter(
      (event) =>
        event.kind === 'progress' &&
        event.command_id === latestProgress?.command_id &&
        (event.stage !== 'implementation' || event.event_id >= implementationTimelineResetId)
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
    const order = (id: string): number => {
      if (id === 'prepare-job') return 10;
      if (id.startsWith('validate-') || id.startsWith('generate-') || id.startsWith('prepare-') || id.startsWith('verify-') || id === 'plan-workflow') return 20;
      if (id === 'phase-backend') return 100;
      if (id === 'phase-frontend') return 200;
      if (id === 'phase-e2e') return 300;
      return 400;
    };
    return [...steps.values()].sort((left, right) => order(left.id) - order(right.id));
  });
  let preparationSubtasks = $derived(
    progressSteps.filter((step) =>
      ['validate-input', 'generate-sources', 'prepare-build', 'verify-generated', 'plan-workflow'].includes(step.id)
    )
  );
  let visibleEvents = $derived.by(() => {
    const lastProgressId = latestProgress?.event_id;
    return events.filter(
      (event) =>
        // The implementation approval is represented by the action controls
        // in Composer.  Rendering the backend's action_required event in the
        // chat timeline as well makes each implementation phase look like a
        // separate approval request (especially when a later phase is queued).
        // Keep the command/result intact so the approval control remains
        // available, but suppress this redundant chatbot message.
        !(
          event.stage === 'implementation' &&
          event.kind === 'action_required'
        ) &&
        !(
          event.stage === 'implementation' &&
          implementationTimelineResetId > 0 &&
          event.event_id < implementationTimelineResetId
        ) &&
        !(
          event.stage === 'implementation' &&
          event.kind === 'progress' &&
          implementationCompletionEventId > 0 &&
          event.event_id < implementationCompletionEventId
        ) &&
        !(
          event.stage === 'implementation' &&
          event.metadata?.reset_implementation_timeline === true
        ) &&
        (event.kind !== 'status' || eventArtifactStages(event).length > 0) &&
        (event.kind !== 'progress' ||
          event.event_id === lastProgressId ||
          event.metadata?.progress_event === 'designLlmMetrics')
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
      (event.kind === 'progress' ||
        (event.kind === 'status' &&
          String(event.metadata?.status ?? '') === 'COMPLETED') ||
        (event.actor === 'assistant' &&
          String(event.metadata?.status ?? '') === 'COMPLETED'))
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

  function eventText(event: WorkspaceEvent): string {
    if (event.stage === 'implementation' && event.kind === 'error') {
      return 'An implementation error occurred. Review the detailed error log below.';
    }
    if (
      event.stage === 'implementation' &&
      event.kind === 'status' &&
      String(event.metadata?.status ?? '') === 'COMPLETED'
    ) {
      return 'Review the generated implementation artifacts below.';
    }
    return event.text;
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
    {@const llmTimings = Array.isArray(event.metadata?.llm_timing_events) ? event.metadata.llm_timing_events : []}
    {#if event.kind === 'progress'}
      <div
        class="mb-4 ml-11 rounded-xl border border-[#dfe3dc] bg-[#fafbf8] px-3 py-2.5 text-xs text-[#555950]"
        data-kind="progress"
      >
        <div class="mb-2 flex items-center justify-between gap-3">
          <span class="font-semibold text-[#343831]">
            {llmTimings.length
              ? 'LLM 실행 기록'
              : String(event.metadata?.progress_card_label ?? 'Requirements analysis')}
          </span>
          <time class="text-[10px] text-[#a0a29a]">{formatTime(event.created_at)}</time>
        </div>
        <div class="space-y-2">
          {#if llmTimings.length}
            <details class="rounded-lg border border-[#dfe3dc] bg-white px-3 py-2">
              <summary class="cursor-pointer font-semibold text-[#343831]">
                LLM 원문 응답 {llmTimings.length}건
              </summary>
              <div class="mt-2 space-y-2">
                {#each llmTimings as timing, index}
                  <details class="rounded-md border border-[#e5e7e1] bg-[#fafbf8] px-2.5 py-2">
                    <summary class="cursor-pointer font-mono text-[10px] text-[#555950]">
                      {index + 1}. {String(timing.operation ?? 'LLM')}
                      · {String(timing.status ?? 'unknown')}
                    </summary>
                    {#if timing.reasoningContent}
                      <h4 class="mb-1 mt-2 font-semibold text-[#555950]">Reasoning</h4>
                      <pre class="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-[#f2f3ef] p-2 text-[10px] leading-4">{String(timing.reasoningContent)}</pre>
                    {/if}
                    {#if timing.responseContent}
                      <h4 class="mb-1 mt-2 font-semibold text-[#555950]">Response</h4>
                      <pre class="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-[#f2f3ef] p-2 text-[10px] leading-4">{String(timing.responseContent)}</pre>
                    {/if}
                    {#if Array.isArray(timing.schemaValidationErrors) && timing.schemaValidationErrors.length}
                      <h4 class="mb-1 mt-2 font-semibold text-[#8a473f]">Schema validation</h4>
                      <pre class="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-[#fff3f1] p-2 text-[10px] leading-4">{JSON.stringify(timing.schemaValidationErrors, null, 2)}</pre>
                    {/if}
                  </details>
                {/each}
              </div>
            </details>
          {:else if progressSteps.length === 0}
            <div class="flex items-center gap-2">
              <LoaderCircle size={13} class="shrink-0 animate-spin text-[#2d7354]" />
              <span>{event.text}</span>
            </div>
          {:else}
            {#each progressSteps.filter((step) => !['validate-input', 'generate-sources', 'prepare-build', 'verify-generated', 'plan-workflow'].includes(step.id)) as step (step.id)}
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
                  {#if step.id === 'prepare-job' && preparationSubtasks.length}
                    <ul class="mt-1.5 space-y-1 border-l-2 border-[#dce3dd] pl-3 text-[10px] leading-4 text-[#62675f]">
                      {#each preparationSubtasks as task (task.id)}
                        <li class="flex items-start gap-1.5">
                          {#if task.status === 'completed'}
                            <CheckCircle2 size={11} class="mt-0.5 shrink-0 text-[#5d806c]" />
                          {:else if task.status === 'failed' || task.status === 'timeout' || task.status === 'needs_review'}
                            <AlertTriangle size={11} class="mt-0.5 shrink-0 text-[#a8433a]" />
                          {:else}
                            <LoaderCircle size={11} class="mt-0.5 shrink-0 animate-spin text-[#2d7354]" />
                          {/if}
                          <span>{task.label}</span>
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
          class="whitespace-pre-wrap rounded-2xl border px-4 text-sm leading-6 shadow-[0_1px_2px_rgba(0,0,0,.02)] {event.actor === 'user'
            ? 'py-3 border-[#d8e5dd] bg-[#edf5f0]'
            : event.kind === 'error' && event.stage === 'implementation'
              ? 'pb-1 pt-3 border-[#eccbc7] bg-[#fff7f6]'
              : event.kind === 'error'
                ? 'py-3 border-[#eccbc7] bg-[#fff7f6]'
                : 'py-3 border-[#e3e3dd] bg-white'}"
        >
          {eventText(event)}
          {#if event.event_id === latestImplementationError && implementationErrors.length}
            <ImplementationErrorPanel errors={implementationErrors} />
          {/if}
          {#if event.metadata?.resource_question?.why}
            <p class="mt-2 border-t border-[#ece8dc] pt-2 text-xs leading-5 text-[#777267]">
              {event.metadata.resource_question.why}
            </p>
          {/if}
          {#if Array.isArray(event.metadata?.blocking_findings) && event.metadata.blocking_findings.length}
            <ul class="mt-2 space-y-1 border-t border-[#ece8dc] pt-2 text-xs leading-5 text-[#76554f]">
              {#each event.metadata.blocking_findings as finding}
                <li><span class="font-mono text-[10px]">{finding.code}</span> · {finding.message}</li>
              {/each}
            </ul>
          {/if}
          {#if event.metadata?.repair_state?.attempt_count > 0}
            <p class="mt-2 text-[11px] leading-5 text-[#777267]">
              Repair attempts: {event.metadata.repair_state.attempt_count}
              · accepted: {event.metadata.repair_state.accepted_count}
              · status: {event.metadata.repair_state.status}
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
