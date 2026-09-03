import type { WorkspaceCommand } from '$lib/types';

// 자동 모드는 별도의 판단 규칙을 가진 실행기가 아니다. 현재 화면에서 사용자가 누를 수
// 있는 action 가운데 추가 판단이 필요 없는 것을 골라 같은 API 요청을 대신 보내는 helper다.

export interface AutoModeAction {
  action: string;
  extra?: Record<string, unknown>;
}

export function nextAutoAction(
  command: WorkspaceCommand | null | undefined
): AutoModeAction | null {
  const offers = command?.result?.actions;
  if (!Array.isArray(offers)) return null;

  const offer = offers.find((candidate) => candidate?.auto_selectable === true);
  return offer ? { action: offer.action, extra: offer.payload } : null;
}
