import type { WorkspaceCommand } from '$lib/types';

export interface AutoModeAction {
  action: string;
  extra?: Record<string, unknown>;
}

function currentResourceQuestion(result: Record<string, any>) {
  if (result.resource_question && typeof result.resource_question === 'object') {
    return result.resource_question;
  }
  if (Array.isArray(result.resource_questions)) {
    return result.resource_questions.find(
      (question) => question && typeof question === 'object'
    );
  }
  return null;
}

export function nextAutoAction(
  command: WorkspaceCommand | null | undefined
): AutoModeAction | null {
  if (!command) return null;
  const result = command.result ?? {};

  if (command.status === 'AWAITING_INPUT') {
    if (result.action === 'confirm_change') return null;

    const resourceQuestion = currentResourceQuestion(result);
    if (resourceQuestion) {
      return resourceQuestion.kind === 'suggested'
        ? { action: 'advance', extra: { action_id: command.command_id } }
        : null;
    }

    if (
      result.kind === 'question' ||
      (Array.isArray(result.questions) && result.questions.length > 0)
    ) {
      return null;
    }

    if (command.stage === 'design' && result.requires_revision === true) {
      return null;
    }

    if (command.stage === 'requirements' || command.stage === 'design') {
      return { action: 'advance', extra: { action_id: command.command_id } };
    }

    if (
      command.stage === 'implementation' &&
      result.job_id &&
      result.request_id
    ) {
      return {
        action: 'approve_implementation',
        extra: {
          action_id: command.command_id,
          job_id: result.job_id,
          request_id: result.request_id,
          delegate_repair_approvals: true
        }
      };
    }
    return null;
  }

  if (command.status !== 'COMPLETED') return null;
  if (command.stage === 'requirements') return { action: 'start_design' };
  if (command.stage === 'design') {
    return {
      action: 'start_implementation',
      extra: { allow_assumptions: true }
    };
  }
  if (command.stage === 'implementation' && result.job_id) {
    return {
      action: 'start_testing',
      extra: { implementation_job_id: result.job_id }
    };
  }
  return null;
}
