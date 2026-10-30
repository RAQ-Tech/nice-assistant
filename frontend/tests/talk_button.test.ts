import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RecordingController } from '../src/recording';
import { state } from '../src/state';
import { talkButton, transcriptionDestination } from '../src/talk_button';
import type { Settings } from '../src/types';

function withSettings(values: Partial<Settings>): void {
  state.settings = { stt_provider: 'openai', stt_hands_free: false, ...values } as Settings;
  state.phase = 'idle';
  state.recordingStartedAt = 0;
}

function fakeRecording(): RecordingController {
  return { start: vi.fn(), stop: vi.fn() } as unknown as RecordingController;
}

describe('Where a recording goes', () => {
  it('says so next to the button when transcription is cloud-based', () => {
    withSettings({ stt_provider: 'openai' });

    const node = talkButton(false, () => undefined, fakeRecording());

    // A person about to speak is the one who needs to know this, and they are
    // not reading a settings page mid-conversation.
    expect(node.querySelector('[data-testid="talk-destination"]')?.textContent)
      .toBe('Recordings are sent to OpenAI to be transcribed.');
    expect(node.querySelector('[data-testid="talk-button"]')).not.toBeNull();
  });

  it('leads to setting talking up when nothing can transcribe', () => {
    withSettings({ stt_provider: 'disabled' });
    const recording = fakeRecording();

    const node = talkButton(false, () => undefined, recording);

    expect(transcriptionDestination()).toBeNull();
    // Nothing is being sent anywhere, so a warning would be noise.
    expect(node.querySelector('[data-testid="talk-destination"]')).toBeNull();
    // "Hold to Talk" here used to end in a red error in the conversation.
    // It is a link to the page that makes it work instead, and never records.
    expect(node.tagName).toBe('A');
    expect(node.dataset.testid).toBe('talk-setup');
    expect(node.getAttribute('href')).toBe('#/settings/STT');
    expect(node.textContent).toBe('Set up talking');
    node.click();
    expect(recording.start).not.toHaveBeenCalled();
  });

  it('says nothing once transcription happens on this machine', () => {
    withSettings({ stt_provider: 'local' });

    const node = talkButton(false, () => undefined, fakeRecording());

    // The note was written to disappear on its own when local transcription
    // arrived, rather than needing somebody to remember to delete it. It did.
    expect(transcriptionDestination()).toBeNull();
    expect(node.querySelector('[data-testid="talk-destination"]')).toBeNull();
  });

  it('still records when the note is showing', () => {
    withSettings({ stt_provider: 'openai', stt_hands_free: true });
    const recording = fakeRecording();

    const node = talkButton(false, () => undefined, recording);
    (node.querySelector('[data-testid="talk-button"]') as HTMLButtonElement).click();

    expect(recording.start).toHaveBeenCalledWith(true);
  });
});

describe('Holding to talk', () => {
  afterEach(() => vi.restoreAllMocks());

  function held(): { button: HTMLButtonElement; recording: RecordingController; clock: { now: number } } {
    withSettings({ stt_provider: 'local', stt_hands_free: false });
    const recording = { start: vi.fn(), stop: vi.fn(), cancel: vi.fn() } as unknown as RecordingController;
    const clock = { now: 1000 };
    vi.spyOn(performance, 'now').mockImplementation(() => clock.now);
    const button = talkButton(false, () => undefined, recording) as HTMLButtonElement;
    document.body.replaceChildren(button);
    return { button, recording, clock };
  }

  const press = (target: EventTarget, type: string, init: PointerEventInit = {}) =>
    target.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, isPrimary: true, button: 0, ...init }));

  it('sends when the finger lifts, wherever it lifts', () => {
    const { button, recording, clock } = held();
    press(button, 'pointerdown');
    expect(recording.start).toHaveBeenCalled();
    // Drifting off the edge mid-sentence is not letting go.
    press(button, 'pointerleave');
    expect(recording.stop).not.toHaveBeenCalled();
    clock.now += 2000;
    press(window, 'pointerup');
    expect(recording.stop).toHaveBeenCalledTimes(1);
    expect(recording.cancel).not.toHaveBeenCalled();
  });

  it('survives the button being redrawn under the finger', () => {
    const { button, recording, clock } = held();
    press(button, 'pointerdown');
    document.body.replaceChildren(talkButton(false, () => undefined, recording));
    clock.now += 1500;
    press(window, 'pointerup');
    expect(recording.stop).toHaveBeenCalledTimes(1);
  });

  it('discards a brush against the button instead of sending it', () => {
    const { button, recording, clock } = held();
    press(button, 'pointerdown');
    clock.now += 100;
    press(window, 'pointerup');
    expect(recording.cancel).toHaveBeenCalledTimes(1);
    expect(recording.stop).not.toHaveBeenCalled();
  });

  it('ignores a second finger and other mouse buttons', () => {
    const { button, recording } = held();
    press(button, 'pointerdown', { isPrimary: false, pointerId: 2 });
    press(button, 'pointerdown', { button: 2 });
    expect(recording.start).not.toHaveBeenCalled();
  });

  it('can be held from the keyboard', () => {
    const { button, recording, clock } = held();
    button.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    button.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', repeat: true, bubbles: true }));
    expect(recording.start).toHaveBeenCalledTimes(1);
    clock.now += 1200;
    button.dispatchEvent(new KeyboardEvent('keyup', { key: ' ', bubbles: true }));
    expect(recording.stop).toHaveBeenCalledTimes(1);
  });
});
