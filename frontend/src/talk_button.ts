import type { ConversationLoop } from './conversation_loop';
import { el } from './dom';
import type { RecordingController } from './recording';
import { setupLink } from './settings_page';
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
  // With nothing to transcribe, a button that says "Hold to Talk" is a promise
  // that ends in an error. It becomes the way to the page that keeps it.
  if ((state.settings?.stt_provider ?? 'disabled') === 'disabled') {
    return setupLink('Set up talking', 'STT', 'talk-setup', {
      className: 'talk-btn talk-setup',
      hover: 'Talk instead of typing: choose what turns your voice into words.',
    });
  }
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
    onpointerdown: (event: PointerEvent) => {
      // One finger, or the main mouse button. A second finger landing is not a
      // second recording.
      if (!event.isPrimary || event.button !== 0) return;
      event.preventDefault();
      beginHold(event.pointerId, stopSpeaking, recording);
    },
    // A long press on a phone otherwise opens a menu, which ends the touch.
    oncontextmenu: (event: Event) => event.preventDefault(),
    onkeydown: (event: KeyboardEvent) => {
      if ((event.key !== ' ' && event.key !== 'Enter') || event.repeat) return;
      event.preventDefault();
      beginHold(KEYBOARD, stopSpeaking, recording);
    },
    onkeyup: (event: KeyboardEvent) => {
      if (event.key === ' ' || event.key === 'Enter') endHold(KEYBOARD, recording);
    },
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

/** Shorter than this, a press was a brush against the button rather than a turn. */
export const BRIEF_PRESS_MS = 350;
const KEYBOARD = -1;

let held: { pointer: number; since: number; release: (event: PointerEvent) => void } | null = null;

/**
 * Start a held recording, and end it however the hold ends.
 *
 * The end is listened for on the window, not the button. The page is redrawn
 * as recording starts, which replaces the button under the finger; and a
 * finger that drifts off the edge mid-sentence has not let go. Only lifting it
 * - anywhere - is the decision. The button's `touch-action: none` keeps the
 * browser from turning a slightly moving hold into a scroll and cancelling it.
 */
function beginHold(pointer: number, stopSpeaking: () => void, recording: RecordingController): void {
  if (held) return;
  const release = (event: PointerEvent) => endHold(event.pointerId, recording);
  held = { pointer, since: performance.now(), release };
  if (pointer !== KEYBOARD) {
    window.addEventListener('pointerup', release);
    window.addEventListener('pointercancel', release);
  }
  stopSpeaking();
  void recording.start();
}

function endHold(pointer: number, recording: RecordingController): void {
  if (!held || held.pointer !== pointer) return;
  const brief = performance.now() - held.since < BRIEF_PRESS_MS;
  window.removeEventListener('pointerup', held.release);
  window.removeEventListener('pointercancel', held.release);
  held = null;
  // Nothing worth sending fits in a brush; discard it rather than transcribe a click.
  if (brief) recording.cancel();
  else void recording.stop();
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
