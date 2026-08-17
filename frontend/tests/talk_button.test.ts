import { describe, expect, it, vi } from 'vitest';

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

  it('says nothing when transcription is off', () => {
    withSettings({ stt_provider: 'disabled' });

    const node = talkButton(false, () => undefined, fakeRecording());

    expect(transcriptionDestination()).toBeNull();
    // Nothing is being sent anywhere, so a warning would be noise.
    expect(node.querySelector('[data-testid="talk-destination"]')).toBeNull();
    expect((node as HTMLButtonElement).dataset.testid).toBe('talk-button');
  });

  it('says nothing once transcription happens on this machine', () => {
    withSettings({ stt_provider: 'local' });

    // Local transcription does not exist yet. When it does, the note has to
    // disappear on its own rather than needing somebody to remember it.
    expect(transcriptionDestination()).toBeNull();
  });

  it('still records when the note is showing', () => {
    withSettings({ stt_provider: 'openai', stt_hands_free: true });
    const recording = fakeRecording();

    const node = talkButton(false, () => undefined, recording);
    (node.querySelector('[data-testid="talk-button"]') as HTMLButtonElement).click();

    expect(recording.start).toHaveBeenCalledWith(true);
  });
});
