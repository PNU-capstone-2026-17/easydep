<script lang="ts">
  import type { TestingRunView } from '$lib/testing-results';
  let { plans }: { plans: TestingRunView['plans'] } = $props();
  let completed = $derived(
    plans.filter((plan) => ['PASS', 'REUSED', 'FAIL', 'INCONCLUSIVE', 'DEFERRED', 'SKIPPED'].includes(plan.status)).length
  );
  function label(plan: TestingRunView['plans'][number]) {
    if (plan.status === 'RUNNING') return (plan.attempt ?? 1) > 1 ? '수정 중' : '생성 중';
    return ({ PASS: '생성 완료', REUSED: '기존 계획 재사용', FAIL: '생성 실패', DEFERRED: '앞선 오류로 미생성' } as Record<string, string>)[plan.status] ?? '대기';
  }
</script>

{#if plans.length}
  <section class="mt-3 border-t border-[#ece8dc] pt-3" aria-label="유스케이스별 테스트 계획 생성">
    <div class="flex justify-between gap-2 text-xs font-semibold text-[#555950]">
      <h4>테스트 계획 생성</h4>
      <span aria-live="polite">{completed}/{plans.length} 완료</span>
    </div>
    <ul class="mt-2 max-h-64 space-y-1 overflow-y-auto">
      {#each plans as plan (plan.workflowId)}
        <li class="rounded-lg px-2 py-2 text-xs {plan.status === 'RUNNING' ? 'bg-[#f6f0df]' : 'bg-[#f7f8f5]'}">
          <div class="flex items-start justify-between gap-3">
            <span class="min-w-0 break-words text-[#454b43]"><strong>{plan.useCaseId}</strong> · {plan.name}</span>
            <span class="shrink-0 font-medium {plan.status === 'FAIL' ? 'text-[#a24037]' : plan.status === 'RUNNING' ? 'text-[#986f22]' : ['PENDING', 'DEFERRED'].includes(plan.status) ? 'text-[#777970]' : 'text-[#2d7354]'}">{label(plan)}</span>
          </div>
          {#if plan.status === 'FAIL' && plan.detail}
            <p class="mt-1 break-words text-[11px] text-[#a24037]">{plan.detail}</p>
          {/if}
        </li>
      {/each}
    </ul>
  </section>
{/if}
