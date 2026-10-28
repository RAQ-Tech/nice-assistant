/**
 * The one Web Audio context the page uses, kept running.
 *
 * A phone's browser starts an audio context suspended unless it was created
 * or resumed during a tap, and a suspended context hears nothing and plays
 * nothing. A context made after a network wait - which is when the microphone
 * meter and the visualizer used to make theirs - is never inside a tap. So
 * there is one context, created and resumed on the first tap anywhere on the
 * page and resumed again on any later tap if the system paused it (a phone call
 * does), and everything that needs one borrows it rather than making its own.
 */

interface AudioWindow {
  AudioContext?: typeof AudioContext;
  webkitAudioContext?: typeof AudioContext;
}

let shared: AudioContext | null = null;

/** The page's audio context, or null where the browser has none. */
export function sharedAudioContext(): AudioContext | null {
  if (shared) return shared;
  const scope = globalThis as AudioWindow;
  const AudioContextClass = scope.AudioContext ?? scope.webkitAudioContext;
  if (!AudioContextClass) return null;
  shared = new AudioContextClass();
  return shared;
}

/**
 * Make sure audio can run. Called from a tap, which is the only moment a
 * phone allows it; harmless anywhere else.
 */
export function resumeAudio(): void {
  const context = sharedAudioContext();
  if (context && context.state !== 'running') void context.resume().catch(() => undefined);
}

/** Forget the context. For tests, which each need a fresh one. */
export function resetSharedAudioContext(): void {
  shared = null;
}

/**
 * Let replies play on a phone: resume audio on every tap and key, and unlock
 * the reply's element on the first.
 *
 * A phone plays sound from an element only after that element has played
 * during a tap. A reply starts after a network wait, never during one, so
 * without this the first spoken reply was blocked and said "Audio is ready.
 * Use replay to listen." The first tap anywhere plays a twentieth of a second
 * of silence through the element, and from then on it may speak.
 */
export function unlockOnFirstTap(audio: HTMLAudioElement, target: EventTarget = window): void {
  let unlocked = false;
  const unlock = (): void => {
    resumeAudio();
    if (unlocked) return;
    unlocked = true;
    // Already playing, or holding a reply, is already past the lock.
    if (!audio.paused || audio.src) return;
    const silence = silentClip();
    audio.src = silence;
    void audio.play().then(
      () => { if (audio.src === silence) audio.pause(); },
      () => { unlocked = false; },
    );
  };
  target.addEventListener('pointerdown', unlock, { passive: true });
  target.addEventListener('keydown', unlock);
}

/** A twentieth of a second of silence, as a WAV data URL. */
function silentClip(): string {
  const samples = 400;
  const bytes = new Uint8Array(44 + samples);
  const view = new DataView(bytes.buffer);
  const ascii = (offset: number, text: string): void => {
    for (let index = 0; index < text.length; index += 1) bytes[offset + index] = text.charCodeAt(index);
  };
  ascii(0, 'RIFF');
  view.setUint32(4, 36 + samples, true);
  ascii(8, 'WAVEfmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, 8000, true);
  view.setUint32(28, 8000, true);
  view.setUint16(32, 1, true);
  view.setUint16(34, 8, true);
  ascii(36, 'data');
  view.setUint32(40, samples, true);
  bytes.fill(128, 44); // the midpoint of unsigned 8-bit audio is silence
  return `data:audio/wav;base64,${btoa(String.fromCharCode(...bytes))}`;
}
