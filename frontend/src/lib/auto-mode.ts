import type { WorkspaceCommand } from '$lib/types';

// 자동 모드는 별도의 판단 규칙을 가진 실행기가 아니다. 현재 화면에서 사용자가 누를 수
// 있는 action 가운데 추가 판단이 필요 없는 것을 골라 같은 API 요청을 대신 보내는 helper다.

export interface AutoModeAction {
  action: string;
  extra?: Record<string, unknown>;
}

function currentResourceQuestion(result: Record<string, any>) {
  // 백엔드는 현재 질문 한 건과 질문 목록 형식을 모두 지원한다. 자동 모드는 첫 질문만
  // 확인하되, 사용자의 직접 선택이 필요한 질문에는 답을 추측하지 않는다.
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
  // null은 자동 모드가 멈춰야 한다는 뜻이다. 호출자는 임의의 기본 action을 만들지 말고
  // 화면에 백엔드가 보낸 선택지를 그대로 표시해야 한다.
  if (!command) return null;
  const result = command.result ?? {};
  const hasPendingMethodProposals =
    command.stage === 'design' &&
    Array.isArray(result.method_proposals) &&
    result.method_proposals.length > 0;

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

    if (result.requires_revision === true && !hasPendingMethodProposals) {
      return null;
    }

    if (command.stage === 'requirements' || command.stage === 'design') {
      return {
        action: 'advance',
        extra: {
          action_id: command.command_id,
          // MethodProposal은 백엔드가 "이 제안을 적용"이라는 선택지로 공개한 상태다.
          // 자동 모드는 사용자가 켠 경우 그 선택지를 대신 누를 뿐, 새 method를 판단하지 않는다.
          ...(hasPendingMethodProposals
            ? { auto_approve_method_proposals: true }
            : {})
        }
      };
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
