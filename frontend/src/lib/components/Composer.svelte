<script lang="ts">
  import { ArrowUp, Check, ChevronRight, LoaderCircle, Paperclip, Play, RotateCcw, X, Zap } from '@lucide/svelte';
  import type { WorkspaceCommand } from '$lib/types';
  import { Button } from '$lib/components/ui/button';

  let {
    command,
    busy,
    context,
    autoMode,
    onSend,
    onAction,
    onToggleAutoMode
  }: {
    command?: WorkspaceCommand | null;
    busy: boolean;
    context?: { stage: string; artifact_stage?: string } | null;
    autoMode: boolean;
    onSend: (text: string) => Promise<void>;
    onAction: (action: string, extra?: Record<string, unknown>) => Promise<void>;
    onToggleAutoMode: () => void;
  } = $props();
  let text = $state('');

  let awaiting = $derived(command?.status === 'AWAITING_INPUT');
  let result = $derived(command?.result ?? {});
  let confirmation = $derived(result?.action === 'confirm_change');
  let resourceQuestion = $derived(
    result?.resource_question ?? result?.resource_questions?.[0] ?? null
  );
  let requiresDesignRevision = $derived(Boolean(result?.requires_revision));
  let implementationAction = $derived(
    command?.stage === 'implementation' &&
      ['approve_implementation', 'rerun_implementation', 'start_implementation'].includes(command.action)
      ? command.action
      : null
  );
  let implementationResponse = $derived(
    implementationAction && ['QUEUED', 'RUNNING'].includes(command?.status ?? '')
      ? implementationAction === 'approve_implementation'
        ? command?.status === 'QUEUED'
          ? 'Implementation approval received. Queuing resume…'
          : 'Implementation approval received. Resuming implementation…'
        : command?.status === 'QUEUED'
          ? 'Implementation retry requested. Queuing a new run…'
          : 'Implementation retry requested. Preparing a new implementation run…'
      : implementationAction && command?.status === 'COMPLETED'
        ? String(result?.message ?? 'Implementation request completed.')
        : implementationAction && command?.status === 'FAILED'
          ? String(command.error ?? 'The implementation request failed.')
          : null
  );

  async function submit() {
    const value = text.trim();
    if (!value || busy) return;
    text = '';
    await onSend(value);
  }

  function keydown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }
</script>

<div class="mx-auto w-full max-w-3xl shrink-0 px-5 pb-5">
  {#if implementationResponse}
    <div
      class="mb-2 rounded-xl border p-2.5 text-xs {command?.status === 'FAILED'
        ? 'border-[#eccbc7] bg-[#fff7f6] text-[#85524c]'
        : command?.status === 'COMPLETED'
          ? 'border-[#cfe3d5] bg-[#f1f8f3] text-[#2d7354]'
          : 'border-[#cfe3d5] bg-[#f1f8f3] text-[#2d7354]'}"
      role="status"
      aria-live="polite"
    >
      <span class="flex items-center gap-2">
        {#if ['QUEUED', 'RUNNING'].includes(command?.status ?? '')}
          <LoaderCircle size={13} class="shrink-0 animate-spin text-[#2d7354]" />
        {/if}
        {implementationResponse}
      </span>
    </div>
  {/if}
  {#if awaiting}
    <div class="mb-2 flex flex-wrap items-center gap-2 rounded-xl border border-[#e5ddc9] bg-[#fffaf0] p-2.5">
      {#if confirmation}
        <Button size="sm" onclick={() => onAction('confirm_change', { action_id: command?.command_id })} disabled={busy}>
          <RotateCcw size={13} /> Return to the earlier stage
        </Button>
        <Button size="sm" variant="ghost" onclick={() => onAction('dismiss_change', { action_id: command?.command_id })} disabled={busy}>
          Keep current artifacts
        </Button>
      {:else if resourceQuestion}
        <span class="px-1 text-xs text-[#74520c]">Reply to the question below.</span>
        {#if resourceQuestion.kind === 'suggested'}
          <Button size="sm" variant="ghost" onclick={() => onAction('advance', { action_id: command?.command_id })} disabled={busy}>
            Continue without this optional input <ChevronRight size={13} />
          </Button>
        {/if}
      {:else if command?.stage === 'design' && requiresDesignRevision}
        <span class="px-1 text-xs text-[#74520c]">
          Review the draft findings and describe the required revision in the message box.
        </span>
      {:else if command?.stage === 'design'}
        <Button size="sm" onclick={() => onAction('advance', { action_id: command?.command_id })} disabled={busy}>
          Continue to the next design step <ChevronRight size={13} />
        </Button>
      {:else if command?.stage === 'implementation' && result?.request_id}
        <Button size="sm" onclick={() => onAction('approve_implementation', { action_id: command?.command_id, job_id: result.job_id, request_id: result.request_id, delegate_repair_approvals: true })} disabled={busy}>
          <Check size={13} /> Approve implementation
        </Button>
        <Button size="sm" variant="danger" onclick={() => onAction('reject_implementation', { action_id: command?.command_id, job_id: result.job_id, request_id: result.request_id })} disabled={busy}>
          <X size={13} /> Reject
        </Button>
      {:else}
        <span class="px-1 text-xs text-[#74520c]">Enter an answer or confirm to continue.</span>
        <Button size="sm" variant="outline" onclick={() => onAction('advance', { action_id: command?.command_id })} disabled={busy}>
          Confirm and continue
        </Button>
      {/if}
    </div>
  {:else if command?.status === 'FAILED' && command.stage === 'design'}
    <div class="mb-2 flex flex-wrap items-center gap-2 rounded-xl border border-[#eccbc7] bg-[#fff7f6] p-2.5">
      <Button size="sm" onclick={() => onAction('retry_design', { action_id: command?.command_id })} disabled={busy}>
        <RotateCcw size={13} /> Retry the failed design step
      </Button>
      <span class="text-xs text-[#85524c]">Completed design artifacts will be kept.</span>
    </div>
  {:else if command?.status === 'FAILED' && command.stage === 'implementation'}
    <div class="mb-2 flex flex-wrap items-center gap-2 rounded-xl border border-[#eccbc7] bg-[#fff7f6] p-2.5">
      <Button size="sm" onclick={() => onAction('rerun_implementation', { base_package: 'com.easydep.app', allow_assumptions: true })} disabled={busy}>
        <RotateCcw size={13} /> Retry failed implementation
      </Button>
      <span class="text-xs text-[#85524c]">The app design context will be reused for a fresh implementation pass.</span>
    </div>
  {:else if command?.status === 'COMPLETED' && command.stage === 'requirements'}
    <div class="mb-2"><Button size="sm" onclick={() => onAction('start_design')} disabled={busy}><Play size={13} /> Start design</Button></div>
  {:else if command?.status === 'COMPLETED' && command.stage === 'design'}
    <div class="mb-2"><Button size="sm" onclick={() => onAction('start_implementation')} disabled={busy}><Play size={13} /> Start implementation</Button></div>
  {:else if command?.status === 'COMPLETED' && command.stage === 'implementation'}
    <div class="mb-2 flex flex-wrap items-center gap-2">
      <Button size="sm" onclick={() => onAction('rerun_implementation', { base_package: 'com.easydep.app', allow_assumptions: true })} disabled={busy}><RotateCcw size={13} /> Rerun implementation</Button>
      {#if result?.job_id}
        <Button size="sm" onclick={() => onAction('start_testing', { implementation_job_id: result.job_id })} disabled={busy}><Play size={13} /> Start testing</Button>
      {/if}
    </div>
  {/if}

  {#if context}
    <div class="mb-2 flex items-center gap-2 px-1 text-[11px] text-[#5e6159]">
      <Paperclip size={12} /> Feedback references the {context.artifact_stage ?? context.stage} artifact
    </div>
  {/if}
  <div class="rounded-2xl border border-[#d8d9d2] bg-white p-2 shadow-[0_8px_30px_rgba(31,35,29,.08)] focus-within:border-[#86ad98]">
    <textarea
      bind:value={text}
      onkeydown={keydown}
      rows="2"
      class="max-h-40 min-h-14 w-full resize-none border-0 bg-transparent px-2 py-2 text-sm leading-6 outline-none placeholder:text-[#999b93]"
      placeholder={resourceQuestion?.question ?? (awaiting ? 'Enter an answer or revision request' : 'Enter a request for the current stage')}
      disabled={busy}
    ></textarea>
    <div class="flex items-center justify-between px-1 pb-1">
      <span class="text-[10px] text-[#a0a199]">Enter to send · Shift+Enter for a new line</span>
      <div class="flex items-center gap-1">
        <Button
          size="icon"
          variant={autoMode ? 'default' : 'ghost'}
          class="size-7 rounded-lg"
          onclick={onToggleAutoMode}
          aria-label={autoMode ? 'Disable automatic progress' : 'Enable automatic progress'}
          aria-pressed={autoMode}
          title={autoMode
            ? 'Automatic progress is enabled. Required questions and failures still pause the run.'
            : 'Automatically continue when no answer is required.'}
        >
          <Zap size={14} />
        </Button>
        <Button size="icon" onclick={submit} disabled={busy || !text.trim()} aria-label="Send message">
          <ArrowUp size={16} />
        </Button>
      </div>
    </div>
  </div>
</div>
