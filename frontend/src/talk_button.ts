import { el } from './dom';
import type { RecordingController } from './recording';
import { state } from './state';

/**
 * The microphone button, and where the recording goes.
 *
 * Hands-free turns this into tap-to-start and tap-to-stop, with the product
 * deciding when the turn ended. Held recording is unchanged and stays the
 * dependable one: the release is the decision, and it is never a guess.
 *
 * Transcription is cloud-only today, so pressing this sends audio off the
 * machine. That is said here, next to the button, rather than only on a
 * settings page nobody is reading mid-conversation - a person about to speak is
 * the one who needs to know it.
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
  const button = el('button', {
    class: `talk-btn ${state.phase === 'recording' ? 'active' : ''}`,
    textContent: label,
    disabled: busy && state.phase !== 'recording',
    'data-testid': 'talk-button',
    title: transcriptionDestination() ?? 'Speech is transcribed on this machine.',
    ...(handsFree ? tap : hold),
  });
  const destination = transcriptionDestination();
  if (!destination) return button;
  return el('div', { class: 'talk-control' }, [
    button,
    el('span', { class: 'meta talk-destination', 'data-testid': 'talk-destination', textContent: destination }),
  ]);
}

/**
 * Where a recording is sent, or null when it never leaves this machine.
 *
 * Local transcription is not implemented yet, so today the only working
 * provider is a cloud one. When that changes this returns null and the note
 * disappears on its own rather than needing to be remembered.
 */
export function transcriptionDestination(): string | null {
  const provider = state.settings?.stt_provider ?? 'disabled';
  if (provider === 'disabled' || provider === 'local') return null;
  return 'Recordings are sent to OpenAI to be transcribed.';
}
