import { el } from './dom';
import type { RecordingController } from './recording';
import { state } from './state';

/**
 * The microphone button.
 *
 * Hands-free turns this into tap-to-start and tap-to-stop, with the product
 * deciding when the turn ended. Held recording is unchanged and stays the
 * dependable one: the release is the decision, and it is never a guess.
 */
export function talkButton(
  busy: boolean,
  stopSpeaking: () => void,
  recording: RecordingController,
): HTMLElement {
  const handsFree = Boolean(state.settings?.stt_hands_free);
  const seconds = Math.floor((Date.now() - state.recordingStartedAt) / 1000);
  const label = state.phase === 'recording'
    ? `${handsFree ? 'Listening' : 'Recording'} ${seconds}s`
    : state.phase === 'transcribing'
      ? 'Transcribing…'
      : handsFree ? 'Tap to Talk' : 'Hold to Talk';
  const hold = {
    onpointerdown: () => { stopSpeaking(); void recording.start(); },
    onpointerup: () => void recording.stop(),
    onpointercancel: () => void recording.stop(),
    onpointerleave: (event: PointerEvent) => { if (event.buttons === 1) void recording.stop(); },
  };
  const tap = {
    onclick: () => {
      if (state.phase === 'recording') { void recording.stop(); return; }
      stopSpeaking();
      void recording.start(true);
    },
  };
  return el('button', {
    class: `talk-btn ${state.phase === 'recording' ? 'active' : ''}`,
    textContent: label,
    disabled: busy && state.phase !== 'recording',
    'data-testid': 'talk-button',
    ...(handsFree ? tap : hold),
  });
}
