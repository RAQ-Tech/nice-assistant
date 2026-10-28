import type { ConversationLoop } from './conversation_loop';
import { el } from './dom';
import type { RecordingController } from './recording';
import { state } from './state';

/**
 * The microphone button, and where the recording goes.
 *
 * Hands-free turns this into tap-to-start and tap-to-stop, with the product
 * deciding when the turn ended, and a reply that ends by listening again (see
 * `ConversationLoop`). Held recording is unchanged and stays the dependable
 * one: the release is the decision, and it is never a guess.
 *
 * The button is also the one place that says what is happening - listening,
 * thinking - so nothing else in the conversation has to. While it listens, a
 * ring around it moves with the voice it hears.
 *
 * If the selected provider is a cloud one, pressing this sends audio off the
 * machine, and that is said here rather than only on a settings page nobody is
 * reading mid-conversation - a person about to speak is the one who needs to
 * know it. With a local transcription service selected there is nothing to say,
 * and nothing is said.
 */
export function talkButton(
  busy: boolean,
  stopSpeaking: () => void,
  recording: RecordingController,
  loop?: ConversationLoop,
): HTMLElement {
  const handsFree = Boolean(state.settings?.stt_hands_free);
  const listening = state.phase === 'recording';
  const conversing = Boolean(loop?.running);
  const label = listening
    ? handsFree ? 'Listening…' : 'Release to send'
    : state.phase === 'transcribing'
      ? 'Transcribing…'
      : conversing && ['queued', 'thinking'].includes(state.phase)
        ? 'Thinking…'
        : handsFree ? 'Tap to Talk' : 'Hold to Talk';
  const hold = {
    onpointerdown: () => { stopSpeaking(); void recording.start(); },
    onpointerup: () => void recording.stop(),
    onpointercancel: () => void recording.stop(),
    onpointerleave: (event: PointerEvent) => { if (event.buttons === 1) void recording.stop(); },
  };
  const tap = {
    onclick: () => {
      if (listening) {
        if (recording.heardSpeech) {
          void recording.stop();
          return;
        }
        // Nothing said yet: a second tap means "never mind", and ends the
        // conversation rather than sending silence to be transcribed.
        loop?.end();
        recording.cancel();
        return;
      }
      stopSpeaking();
      loop?.begin();
      void recording.start(true);
    },
  };
  const button = el('button', {
    class: `talk-btn ${listening ? 'active' : ''} ${listening && handsFree ? 'listening' : ''}`,
    textContent: label,
    disabled: busy && !listening,
    'aria-live': 'polite',
    'data-testid': 'talk-button',
    title: listening && handsFree ? 'Tap to send now, or before speaking to stop.' : (transcriptionDestination() ?? ''),
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
 * A local transcription service keeps the recording on this network, so there
 * is no destination to warn about and the note does not appear. Only a cloud
 * provider produces one.
 */
export function transcriptionDestination(): string | null {
  const provider = state.settings?.stt_provider ?? 'disabled';
  if (provider === 'disabled' || provider === 'local') return null;
  return 'Recordings are sent to OpenAI to be transcribed.';
}
