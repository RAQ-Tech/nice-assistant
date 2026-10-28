import { afterEach, describe, expect, it, vi } from 'vitest';

import { resetSharedAudioContext, resumeAudio, unlockOnFirstTap } from '../src/audio_context';
import { PlaybackController } from '../src/playback';
import { ClientStateMachine, createState } from '../src/state';
import { streamableMimeType, streamsThroughManagedSource } from '../src/streaming_audio';
import { Visualizer } from '../src/visualization';

/**
 * A phone plays a reply only if audio was allowed during a tap, and an iPhone
 * streams only through `ManagedMediaSource`. Neither was handled, so on a
 * phone the first reply was blocked and every reply waited to be complete.
 */

afterEach(() => {
  vi.unstubAllGlobals();
  resetSharedAudioContext();
});

function controller() {
  const appState = createState();
  appState.phase = 'idle';
  const audio = document.createElement('audio');
  const play = vi.spyOn(audio, 'play').mockResolvedValue(undefined);
  const pause = vi.spyOn(audio, 'pause').mockImplementation(() => undefined);
  const playback = new PlaybackController(audio, new Visualizer(audio), appState, new ClientStateMachine(appState));
  const page = new EventTarget();
  unlockOnFirstTap(audio, page);
  const tap = () => page.dispatchEvent(new Event('pointerdown'));
  return { audio, play, pause, playback, tap };
}

describe('replies on a phone', () => {
  it('plays a moment of silence on the first tap, so the reply after it may speak', async () => {
    const { audio, play, pause, tap } = controller();
    tap();
    await Promise.resolve();
    expect(play).toHaveBeenCalledTimes(1);
    expect(audio.src.startsWith('data:audio/wav;base64,')).toBe(true);
    expect(pause).toHaveBeenCalled();
    // Once is enough; every later tap leaves the element alone.
    tap();
    expect(play).toHaveBeenCalledTimes(1);
  });

  it('never cuts off a reply that started before the silence finished', async () => {
    const { audio, pause, tap } = controller();
    tap();
    audio.src = '/api/v1/audio/reply.mp3';
    await Promise.resolve();
    expect(pause).not.toHaveBeenCalled();
  });

  it('resumes the shared audio on a tap', () => {
    const resume = vi.fn().mockResolvedValue(undefined);
    class FakeContext {
      state = 'suspended';
      resume = resume;
    }
    vi.stubGlobal('AudioContext', FakeContext);
    resumeAudio();
    expect(resume).toHaveBeenCalled();
  });

  it('leaves the reply to play on its own while the visualizer is off', () => {
    const createMediaElementSource = vi.fn();
    vi.stubGlobal('AudioContext', class { createMediaElementSource = createMediaElementSource; });
    const audio = document.createElement('audio');
    const visualizer = new Visualizer(audio);
    visualizer.setEnabled(false);
    visualizer.connectAudio();
    // Routed through Web Audio, the element is silent whenever that is.
    expect(createMediaElementSource).not.toHaveBeenCalled();
  });

  it('streams through a managed source where that is all an iPhone has', () => {
    vi.stubGlobal('MediaSource', undefined);
    vi.stubGlobal('ManagedMediaSource', { isTypeSupported: (type: string) => type === 'audio/mpeg' });
    expect(streamableMimeType('mp3')).toBe('audio/mpeg');
    expect(streamsThroughManagedSource()).toBe(true);
    const { audio } = controller();
    // Required for a managed source to open at all.
    expect(audio.disableRemotePlayback).toBe(true);
  });

  it('prefers the ordinary source where both exist', () => {
    vi.stubGlobal('MediaSource', { isTypeSupported: () => true });
    vi.stubGlobal('ManagedMediaSource', { isTypeSupported: () => true });
    expect(streamsThroughManagedSource()).toBe(false);
  });
});
