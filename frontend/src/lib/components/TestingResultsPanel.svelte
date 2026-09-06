<script lang="ts">
  import { CheckCircle2, Circle, CircleAlert, Download, LoaderCircle } from '@lucide/svelte';
  import TestingRunCard from '$lib/components/TestingRunCard.svelte';
  import type { TestingRunView, TestingStatus } from '$lib/testing-results';

  let { run }: { run: TestingRunView | null } = $props();
  let tab = $state<'dynamic' | 'static'>('dynamic');
  let showArazzo = $state(false);

  function tone(value: TestingStatus): string {
    if (value === 'PASS' || value === 'REUSED') return 'text-[#2d7354]';
    if (value === 'FAIL') return 'text-[#a24037]';
    if (value === 'RUNNING') return 'text-[#986f22]';
    return 'text-[#777970]';
  }

  function statusIcon(value: TestingStatus) {
    if (value === 'PASS' || value === 'REUSED') return CheckCircle2;
    if (value === 'FAIL') return CircleAlert;
    if (value === 'RUNNING') return LoaderCircle;
    return Circle;
  }

  function json(value: unknown): string {
    return JSON.stringify(value ?? null, null, 2);
  }

  function downloadReport() {
    if (!run?.rawReport) return;
    const url = URL.createObjectURL(
      new Blob([json(run.rawReport)], { type: 'application/json' })
    );
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `testing-${run.commandId ?? 'report'}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }
</script>

{#if !run}
  <div class="mt-16 text-center text-[#898b83]" role="status">
    <p class="text-xs">No Testing run is available yet.</p>
  </div>
{:else}
  <div class="space-y-3 p-3">
    <TestingRunCard {run} />

    <section class="rounded-2xl border border-[#dfe1da] bg-white p-3 text-[11px] text-[#686b63]" aria-label="Testing run details">
      <dl class="grid grid-cols-2 gap-x-3 gap-y-2">
        <div><dt class="text-[9px] font-bold uppercase tracking-wide text-[#92958c]">Command</dt><dd class="truncate font-mono">{run.commandId ?? '—'}</dd></div>
        <div><dt class="text-[9px] font-bold uppercase tracking-wide text-[#92958c]">Implementation</dt><dd class="truncate font-mono">{run.targetImplementationJobId ?? '—'}</dd></div>
        {#if run.gateCounts}
          <div><dt class="text-[9px] font-bold uppercase tracking-wide text-[#92958c]">Gate result</dt><dd>{run.gateCounts.passed} passed · {run.gateCounts.failed} failed</dd></div>
        {/if}
        <div><dt class="text-[9px] font-bold uppercase tracking-wide text-[#92958c]">Last update</dt><dd>{run.updatedAt ?? run.startedAt ?? '—'}</dd></div>
      </dl>
      <div class="mt-3 flex flex-wrap gap-2 border-t border-[#ecece6] pt-3">
        {#if run.rawReport}
          <button class="focus-ring flex items-center gap-1 rounded-md border border-[#d8ddd7] px-2 py-1 font-semibold hover:bg-[#f5f7f3]" onclick={downloadReport}><Download size={12} /> Download report</button>
        {/if}
        {#if run.arazzoDocument}
          <button class="focus-ring rounded-md border border-[#d8ddd7] px-2 py-1 font-semibold hover:bg-[#f5f7f3]" onclick={() => (showArazzo = !showArazzo)}>{showArazzo ? 'Hide Arazzo' : 'View Arazzo'}</button>
        {/if}
      </div>
      {#if showArazzo && run.arazzoDocument}
        <pre class="prose-json mt-3 max-h-72 overflow-auto rounded-lg bg-[#f7f8f5] p-3 text-[10px]">{json(run.arazzoDocument)}</pre>
      {/if}
    </section>

    <div class="grid grid-cols-2 rounded-xl border border-[#dfe1da] bg-[#f1f2ee] p-1" role="tablist" aria-label="Testing result categories">
      <button class="focus-ring rounded-lg px-2 py-2 text-[11px] font-semibold {tab === 'dynamic' ? 'bg-white text-[#285b43] shadow-sm' : 'text-[#74776f]'}" role="tab" aria-selected={tab === 'dynamic'} onclick={() => (tab = 'dynamic')}>Dynamic API Tests</button>
      <button class="focus-ring rounded-lg px-2 py-2 text-[11px] font-semibold {tab === 'static' ? 'bg-white text-[#285b43] shadow-sm' : 'text-[#74776f]'}" role="tab" aria-selected={tab === 'static'} onclick={() => (tab = 'static')}>Static &amp; Deployment Tests</button>
    </div>

    {#if tab === 'dynamic'}
      <section class="rounded-2xl border border-[#dfe1da] bg-white p-4" aria-label="Dynamic API Tests">
        <div class="mb-3 flex items-center justify-between gap-3">
          <h3 class="text-xs font-semibold text-[#30362f]">Arazzo workflows</h3>
          <span class="text-[10px] text-[#85877e]">{run.workflowCounts.passed}/{run.workflowCounts.total} passed</span>
        </div>
        {#if run.workflows.length === 0}
          <p class="text-[11px] text-[#898b83]">Workflow progress has not started.</p>
        {/if}
        <div class="space-y-2">
          {#each run.workflows as workflow}
            {@const Icon = statusIcon(workflow.status)}
            <details class="rounded-xl border border-[#ecece6] p-3" open={workflow.status === 'FAIL' || workflow.status === 'RUNNING'}>
              <summary class="focus-ring flex cursor-pointer list-none items-center gap-2">
                <Icon size={14} class={`${workflow.status === 'RUNNING' ? 'animate-spin' : ''} ${tone(workflow.status)}`} />
                <strong class="min-w-0 flex-1 truncate text-xs">{workflow.label}</strong>
                <span class={`text-[10px] font-semibold ${tone(workflow.status)}`}>{workflow.status}</span>
              </summary>
              {#if workflow.requirementIds.length || workflow.useCaseIds.length}
                <div class="mt-2 flex flex-wrap gap-1">
                  {#each [...workflow.requirementIds, ...workflow.useCaseIds] as trace}
                    <span class="rounded bg-[#edf3ef] px-1.5 py-0.5 font-mono text-[9px] text-[#466351]">{trace}</span>
                  {/each}
                </div>
              {/if}
              {#if workflow.detail}<p class="mt-2 text-[11px] leading-5 text-[#777970]">{workflow.detail}</p>{/if}
              <div class="mt-2 space-y-1 border-t border-[#f0f0eb] pt-2">
                {#each workflow.steps as step}
                  {@const StepIcon = statusIcon(step.status)}
                  {#if step.cleanup}<p class="pt-1 text-[9px] font-bold uppercase tracking-wide text-[#8b8172]">Cleanup path</p>{/if}
                  <details class="rounded-lg bg-[#fafaf7] px-2.5 py-2">
                    <summary class="focus-ring flex cursor-pointer list-none items-center gap-2 text-[11px]">
                      <StepIcon size={12} class={`${step.status === 'RUNNING' ? 'animate-spin' : ''} ${tone(step.status)}`} />
                      <span class="min-w-0 flex-1 truncate">{step.method && step.path ? `${step.method} ${step.path}` : step.label}</span>
                      {#if step.statusCode}<span class="font-mono text-[10px] text-[#74776f]">{step.statusCode}</span>{/if}
                      {#if step.control}<span class="rounded bg-[#eceee9] px-1.5 py-0.5 text-[9px]">{step.control}</span>{/if}
                      {#if step.attempt && step.attempt > 1}<span class="rounded bg-[#fff3d8] px-1.5 py-0.5 text-[9px]">attempt {step.attempt}</span>{/if}
                      <span class={`text-[9px] font-semibold ${tone(step.status)}`}>{step.status}</span>
                    </summary>
                    <div class="mt-2 space-y-1 border-t border-[#eceee9] pt-2 text-[10px] leading-4 text-[#666a61]">
                      {#if step.detail}<p>{step.detail}</p>{/if}
                      {#if step.contractStatus || step.semanticStatus}<p>Contract: {step.contractStatus ?? '—'} · Semantic: {step.semanticStatus ?? '—'}</p>{/if}
                      {#each step.criteria as criterion}<p>Criterion: {criterion.condition} · {criterion.passed == null ? 'not evaluated' : criterion.passed ? 'PASS' : 'FAIL'}</p>{/each}
                      {#if step.request != null || step.response != null}
                        <details class="mt-2"><summary class="cursor-pointer font-semibold">Request and response evidence</summary><pre class="prose-json mt-1 max-h-60 overflow-auto rounded bg-white p-2">{json({ request: step.request, response: step.response })}</pre></details>
                      {/if}
                    </div>
                  </details>
                {/each}
              </div>
            </details>
          {/each}
        </div>
      </section>
    {:else}
      <section class="rounded-2xl border border-[#dfe1da] bg-white p-4" aria-label="Static and deployment tests">
        <h3 class="mb-3 text-xs font-semibold text-[#30362f]">Static verification gates</h3>
        <div class="space-y-2">
          {#each run.gates.filter((gate) => gate.id !== 'dynamicFunctional') as gate}
            {@const GateIcon = statusIcon(gate.status)}
            <details class="rounded-xl border border-[#ecece6] p-3" open={gate.status === 'FAIL' || gate.status === 'RUNNING'}>
              <summary class="focus-ring flex cursor-pointer list-none items-center gap-2">
                <GateIcon size={14} class={`${gate.status === 'RUNNING' ? 'animate-spin' : ''} ${tone(gate.status)}`} />
                <strong class="min-w-0 flex-1 text-[11px]">{gate.label}</strong>
                <span class={`text-[10px] font-semibold ${tone(gate.status)}`}>{gate.status}</span>
              </summary>
              <div class="mt-2 space-y-1 border-t border-[#f0f0eb] pt-2 text-[10px] leading-4 text-[#666a61]">
                {#if gate.detail}<p>{gate.detail}</p>{/if}
                {#if gate.reused}<p>Reused from the previous run.</p>{/if}
                {#if gate.elapsedMs != null}<p>Elapsed: {(gate.elapsedMs / 1000).toFixed(1)}s</p>{/if}
                {#each gate.issues as issue}<p class="text-[#76554f]">{issue}</p>{/each}
                {#if gate.files.length}<p>Files: {gate.files.join(', ')}</p>{/if}
                {#each gate.commands as command}<code class="block break-all rounded bg-[#f7f8f5] px-2 py-1 font-mono">{command}</code>{/each}
              </div>
            </details>
          {/each}
        </div>
      </section>
    {/if}

    {#if run.findings.length}
      <section class="rounded-2xl border border-[#ead4ce] bg-[#fffaf8] p-4" aria-label="Testing findings">
        <h3 class="mb-2 text-xs font-semibold text-[#76554f]">Test result</h3>
        <ul class="space-y-2 text-[11px] leading-5 text-[#76554f]">
          {#each run.findings as finding}
            <li><span class="font-mono text-[10px]">{finding.code}</span> · {finding.message}{#if finding.defectClass}<span class="ml-1 rounded bg-[#f2e3df] px-1.5 py-0.5 text-[9px]">{finding.defectClass}</span>{/if}</li>
          {/each}
        </ul>
      </section>
    {/if}
    {#if run.initialFailure}
      <section class="rounded-2xl border border-[#e6d9bd] bg-[#fffcf4] p-4 text-[11px] leading-5 text-[#715e37]" aria-label="Initial testing failure">
        <div class="flex items-center justify-between gap-2"><strong>Initial failure</strong><span class="font-semibold">{run.initialFailure.gateStatus}</span></div>
        {#if run.initialFailure.reason}<p class="mt-1">{run.initialFailure.reason}</p>{/if}
        {#if run.initialFailure.failedWorkflowId}<p class="mt-1 font-mono text-[10px]">{run.initialFailure.failedWorkflowId}{run.initialFailure.failedStepId ? ` / ${run.initialFailure.failedStepId}` : ''}</p>{/if}
        {#each run.initialFailure.findings as finding}<p class="mt-1"><span class="font-mono text-[10px]">{finding.code}</span> · {finding.message}</p>{/each}
      </section>
    {/if}
    {#if run.repair}
      <section class="rounded-2xl border border-[#d9e4dc] bg-[#f6faf7] p-4 text-[11px] leading-5 text-[#456151]" aria-label="Automatic repair"><strong class="block">Automatic repair</strong><p>{run.repair.status ?? 'ACTIVE'} · {run.repair.attemptCount ?? 0} attempt(s) · {run.repair.acceptedCount ?? 0} accepted</p></section>
    {/if}
    {#if run.systemError}
      <section class="rounded-2xl border border-[#e6bdb8] bg-[#fff5f3] p-4 text-[11px] leading-5 text-[#8b3d36]" aria-label="EasyDep testing error"><strong class="block">EasyDep error</strong>{run.systemError}</section>
    {/if}
  </div>
{/if}
