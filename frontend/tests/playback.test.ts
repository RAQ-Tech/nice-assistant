import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { PlaybackController } from '../src/playback';
import { ClientStateMachine, createState } from '../src/state';
import type { Settings } from '../src/types';
import type { Visualizer } from '../src/visualization';

function readyPlayback() {
  const appState = createState();
  appState.phase = 'idle';
  appState.settings = {
    tts_provider: 'local',
    tts_format: 'wav',
  } as Settings;
  const audio = document.createElement('audio');
  const play = vi.spyOn(audio, 'play').mockResolvedValue(undefined);
  const pause = vi.spyOn(audio, 'pause').mockImplementation(() => undefined);
  const visualizer = { connectAudio: vi.fn() } as unknown as Visualizer;
  return { appState, audio, play, pause, visualizer };
}

describe('PlaybackController', () => {
  it('cleans model markup before requesting completed-file Kokoro speech', async () => {
    const { appState, audio, visualizer } = readyPlayback();
    const client = {
      synthesize: vi.fn().mockResolvedValue({ audio_url: '/api/v1/audio/one.wav' }),
    } as unknown as ApiClient;
    const controller = new PlaybackController(audio, visualizer, appState, new ClientStateMachine(appState), client);

    await controller.synthesize('<think>hidden</think>**Hello** [friend](https://example.test) `now`.', 'message-1', 'chat-1', 'persona-1');

    expect(client.synthesize).toHaveBeenCalledWith(expect.objectContaining({ text: 'Hello friend now.' }));
    expect(appState.phase).toBe('speaking');
  });

  it('invalidates a slow synthesis when the user interrupts playback', async () => {
    const { appState, audio, play, visualizer } = readyPlayback();
    let resolveSpeech!: (value: { audio_url: string }) => void;
    const pendingSpeech = new Promise<{ audio_url: string }>((resolve) => { resolveSpeech = resolve; });
    const client = { synthesize: vi.fn().mockReturnValue(pendingSpeech) } as unknown as ApiClient;
    const controller = new PlaybackController(audio, visualizer, appState, new ClientStateMachine(appState), client);

    const synthesis = controller.synthesize('Slow reply', 'message-1', 'chat-1', 'persona-1');
    controller.stop();
    resolveSpeech({ audio_url: '/api/v1/audio/slow.wav' });
    await synthesis;

    expect(play).not.toHaveBeenCalled();
    expect(appState.currentAudioMessageId).toBeNull();
    expect(appState.phase).toBe('idle');
  });

  it('does not claim speaking until audio playback actually starts', async () => {
    const { appState, audio, visualizer } = readyPlayback();
    let startAudio!: () => void;
    vi.spyOn(audio, 'play').mockReturnValue(new Promise<void>((resolve) => { startAudio = resolve; }));
    const controller = new PlaybackController(
      audio,
      visualizer,
      appState,
      new ClientStateMachine(appState),
      {} as ApiClient,
    );

    const playing = controller.play('message-1', '/api/v1/audio/one.wav');
    expect(appState.phase).toBe('idle');
    startAudio();
    await playing;
    expect(appState.phase).toBe('speaking');
  });
});
