import type { ClientPhase } from './types';

export function composerState(phase: ClientPhase): { busy: boolean; inputLocked: boolean } {
  return {
    busy: ['queued', 'thinking', 'transcribing'].includes(phase),
    inputLocked: phase === 'transcribing',
  };
}
