import assert from 'node:assert/strict';
import test from 'node:test';

import { nextAutoAction } from '../src/lib/auto-mode.ts';

function command(result) {
  return {
    command_id: 'command-1',
    app_id: 'app-1',
    action: 'advance',
    stage: 'requirements',
    status: 'AWAITING_INPUT',
    payload: {},
    result
  };
}

test('auto mode selects the same exposed delegate repair action', () => {
  assert.deepEqual(
    nextAutoAction(
      command({
        requires_revision: true,
        can_delegate_repair: true,
        repair_state: { status: 'ACTIVE' }
      })
    ),
    { action: 'delegate_repair', extra: { action_id: 'command-1' } }
  );
});

test('auto mode never waives blockers with a blank advance', () => {
  assert.equal(
    nextAutoAction(command({ requires_revision: true, can_delegate_repair: false })),
    null
  );
});

test('auto mode pauses while an external LLM is unavailable', () => {
  assert.equal(
    nextAutoAction(
      command({
        requires_revision: true,
        can_delegate_repair: true,
        repair_state: { status: 'WAITING_EXTERNAL' }
      })
    ),
    null
  );
});

test('auto mode pauses after the history proves a repeated failure', () => {
  assert.equal(
    nextAutoAction(
      command({
        requires_revision: true,
        can_delegate_repair: true,
        repair_state: { status: 'STALLED' }
      })
    ),
    null
  );
});
