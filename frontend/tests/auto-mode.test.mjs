import assert from 'node:assert/strict';
import test from 'node:test';

import { nextAutoAction } from '../src/lib/auto-mode.ts';

function command(result, overrides = {}) {
  return {
    command_id: 'command-1',
    app_id: 'app-1',
    action: 'advance',
    stage: 'requirements',
    status: 'AWAITING_INPUT',
    payload: {},
    result,
    ...overrides
  };
}

test('auto mode selects the first backend offer marked auto selectable', () => {
  assert.deepEqual(
    nextAutoAction(command({
      actions: [
        {
          action: 'message',
          label: 'Answer manually',
          payload: { action_id: 'command-1' },
          auto_selectable: false
        },
        {
          action: 'advance',
          label: 'Continue',
          payload: { action_id: 'command-1', approve: true },
          auto_selectable: true
        },
        {
          action: 'start_design',
          label: 'Start design',
          payload: {},
          auto_selectable: true
        }
      ]
    })),
    {
      action: 'advance',
      extra: { action_id: 'command-1', approve: true }
    }
  );
});

test('auto mode does not infer actions from legacy result fields or command state', () => {
  assert.equal(nextAutoAction(command({
    requires_revision: false,
    job_id: 'job-1',
    request_id: 'request-1'
  }, { stage: 'implementation' })), null);
  assert.equal(nextAutoAction(command({}, { status: 'COMPLETED' })), null);
});

test('auto mode stops when no offer is explicitly auto selectable', () => {
  assert.equal(nextAutoAction(command({
    actions: [{
      action: 'message',
      label: 'Provide an answer',
      payload: { action_id: 'command-1' },
      auto_selectable: false
    }]
  })), null);
});
