import type { ClientPhase } from './types';

export function composerState(phase: ClientPhase): { busy: boolean; inputLocked: boolean } {
  return {
    busy: ['queued', 'thinking', 'transcribing'].includes(phase),
    inputLocked: phase === 'transcribing',
  };
}

/**
 * What has been heard so far, to show where it would have been typed, or null.
 *
 * The way dictation does it: the words appear in the message box and leave
 * when they are sent. They arrive at each pause when transcribing at pauses is
 * on; otherwise there is nothing to show until the end.
 */
export function heardSoFar(phase: ClientPhase, partialTranscript: string): string | null {
  return ['recording', 'transcribing'].includes(phase) && partialTranscript ? partialTranscript : null;
}
