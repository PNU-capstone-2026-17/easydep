import assert from 'node:assert/strict';
import test from 'node:test';

import {
  implementationCompletionArtifactLoadKey,
  shouldLoadFileArtifactsInitially
} from '../src/lib/artifacts.ts';

function command(status, stage = 'implementation', commandId = 'command-1') {
  return {
    command_id: commandId,
    app_id: 'app-1',
    action: 'start_implementation',
    stage,
    status,
    payload: {}
  };
}

test('initial load skips unpublished implementation artifacts', () => {
  assert.equal(shouldLoadFileArtifactsInitially(command('RUNNING')), false);
  assert.equal(shouldLoadFileArtifactsInitially(command('COMPLETED')), true);
  assert.equal(shouldLoadFileArtifactsInitially(command('RUNNING', 'testing')), true);
  assert.equal(shouldLoadFileArtifactsInitially(command('COMPLETED', 'design')), false);
});

test('implementation completion requests artifacts once per command', () => {
  assert.equal(
    implementationCompletionArtifactLoadKey(
      command('RUNNING'),
      command('COMPLETED')
    ),
    'command-1'
  );
  assert.equal(
    implementationCompletionArtifactLoadKey(
      command('COMPLETED'),
      command('COMPLETED')
    ),
    null
  );
  assert.equal(
    implementationCompletionArtifactLoadKey(
      command('COMPLETED'),
      command('COMPLETED', 'implementation', 'command-2')
    ),
    'command-2'
  );
  assert.equal(
    implementationCompletionArtifactLoadKey(
      command('RUNNING', 'testing'),
      command('COMPLETED', 'testing')
    ),
    null
  );
});
