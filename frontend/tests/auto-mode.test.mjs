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

test('auto mode leaves automatic repair to the running backend task', () => {
  assert.equal(
    nextAutoAction(command({ requires_revision: true })),
    null
  );
});
