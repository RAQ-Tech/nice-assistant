import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { PlaybackController } from '../src/playback';
import { ClientStateMachine, createState } from '../src/state';
import { streamableMimeType, type AudioStreamSink } from '../src/streaming_audio';
import type { Settings } from '../src/types';
import type { Visualizer } from '../src/visualization';

class RecordingSink implements AudioStreamSink {
  readonly pieces: Uint8Array[] = [];
  ended = false;
  closed = false;

  async open(): Promise<string> {
    return 'blob:stream';
  }

  async append(chunk: Uint8Array): Promise<void> {
    this.pieces.push(chunk);
  }

  async end(): Promise<void> {
    this.ended = true;
  }

  close(): void {
    this.closed = true;
  }
}

function streamOf(pieces: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      pieces.forEach((piece) => controller.enqueue(piece));
      controller.close();
    },
  });
}

function setup(format = 'mp3') {
  const appState = createState();
  appState.phase = 'idle';
  appState.settings = { tts_provider: 'local', tts_format: format } as Settings;
  const audio = document.createElement('audio');
  const play = vi.spyOn(audio, 'play').mockResolvedValue(undefined);
  vi.spyOn(audio, 'pause').mockImplementation(() => undefined);
  const visualizer = { connectAudio: vi.fn() } as unknown as Visualizer;
  const sink = new RecordingSink();
  return { appState, audio, play, visualizer, sink };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('streamableMimeType', () => {
  it('names a format the browser will play while it arrives', () => {
    vi.stubGlobal('MediaSource', { isTypeSupported: (type: string) => type === 'audio/mpeg' });
    expect(streamableMimeType('mp3')).toBe('audio/mpeg');
    // Supported by the format table, refused by this browser.
    expect(streamableMimeType('aac')).toBeNull();
  });

  it('refuses a format that has no incremental form at all', () => {
    vi.stubGlobal('MediaSource', { isTypeSupported: () => true });
    // WAV carries its length in a header nothing can fill in halfway through.
    expect(streamableMimeType('wav')).toBeNull();
  });

  it('refuses everything when the browser has no media source', () => {
    vi.stubGlobal('MediaSource', undefined);
    expect(streamableMimeType('mp3')).toBeNull();
  });
});

describe('Speech that starts before it is finished', () => {
  it('plays the first piece while the rest is still being made', async () => {
    vi.stubGlobal('MediaSource', { isTypeSupported: () => true });
    const { appState, audio, play, visualizer, sink } = setup();
    const client = {
      streamSpeech: vi.fn().mockResolvedValue({
        body: streamOf([new Uint8Array([1]), new Uint8Array([2])]),
        headers: new Headers({ 'X-Nice-Assistant-Audio-Id': 'audio-9' }),
      }),
      synthesize: vi.fn(),
    } as unknown as ApiClient;
    const controller = new PlaybackController(
      audio, visualizer, appState, new ClientStateMachine(appState), client, () => sink,
    );

    await controller.synthesize('Hello there.', 'message-1', 'chat-1', 'persona-1');

    expect(client.streamSpeech).toHaveBeenCalledWith(
      expect.objectContaining({ text: 'Hello there.', format: 'mp3' }),
      expect.any(AbortSignal),
    );
    // The completed-file request is never made: the point is not to wait for it.
    expect(client.synthesize).not.toHaveBeenCalled();
    expect(play).toHaveBeenCalled();
    expect(sink.pieces.map((piece) => piece[0])).toEqual([1, 2]);
    expect(sink.ended).toBe(true);
    expect(sink.closed).toBe(true);
    // Replay still uses the stored recording rather than speaking it again.
    expect(appState.messageAudioById['message-1']).toBe('/api/v1/audio/audio-9');
  });

  it('falls back to the completed file when the format cannot start early', async () => {
    vi.stubGlobal('MediaSource', { isTypeSupported: () => true });
    const { appState, audio, visualizer, sink } = setup('wav');
    const client = {
      streamSpeech: vi.fn(),
      synthesize: vi.fn().mockResolvedValue({ audio_url: '/api/v1/audio/whole.wav' }),
    } as unknown as ApiClient;
    const controller = new PlaybackController(
      audio, visualizer, appState, new ClientStateMachine(appState), client, () => sink,
    );

    await controller.synthesize('Hello there.', 'message-1', 'chat-1', 'persona-1');

    expect(client.streamSpeech).not.toHaveBeenCalled();
    expect(client.synthesize).toHaveBeenCalled();
    expect(appState.messageAudioById['message-1']).toBe('/api/v1/audio/whole.wav');
  });

  it('falls back to the completed file when the stream cannot be started', async () => {
    vi.stubGlobal('MediaSource', { isTypeSupported: () => true });
    const { appState, audio, visualizer, sink } = setup();
    const client = {
      streamSpeech: vi.fn().mockRejectedValue(new Error('no stream here')),
      synthesize: vi.fn().mockResolvedValue({ audio_url: '/api/v1/audio/whole.mp3' }),
    } as unknown as ApiClient;
    const controller = new PlaybackController(
      appState.settings ? audio : audio, visualizer, appState, new ClientStateMachine(appState), client, () => sink,
    );

    await controller.synthesize('Hello there.', 'message-1', 'chat-1', 'persona-1');

    // Nothing was heard yet, so the completed file is a recovery rather than a
    // repetition.
    expect(client.synthesize).toHaveBeenCalled();
    expect(appState.messageAudioById['message-1']).toBe('/api/v1/audio/whole.mp3');
  });

  it('does not speak a reply twice when a stream fails after it started', async () => {
    vi.stubGlobal('MediaSource', { isTypeSupported: () => true });
    const { appState, audio, visualizer, sink } = setup();
    const client = {
      streamSpeech: vi.fn().mockResolvedValue({
        body: new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new Uint8Array([1]));
            controller.error(new Error('provider gave up'));
          },
        }),
        headers: new Headers(),
      }),
      synthesize: vi.fn().mockResolvedValue({ audio_url: '/api/v1/audio/whole.mp3' }),
    } as unknown as ApiClient;
    const controller = new PlaybackController(
      audio, visualizer, appState, new ClientStateMachine(appState), client, () => sink,
    );

    await controller.synthesize('Hello there.', 'message-1', 'chat-1', 'persona-1');

    // Some of it was already heard. Fetching the whole file now would say the
    // beginning again.
    expect(client.synthesize).not.toHaveBeenCalled();
    expect(sink.closed).toBe(true);
  });

  it('aborts the stream when playback is interrupted', async () => {
    vi.stubGlobal('MediaSource', { isTypeSupported: () => true });
    const { appState, audio, visualizer, sink } = setup();
    let seen!: AbortSignal;
    const client = {
      streamSpeech: vi.fn().mockImplementation((_input: unknown, signal: AbortSignal) => {
        seen = signal;
        return new Promise((_resolve, reject) => {
          signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
        });
      }),
      synthesize: vi.fn(),
    } as unknown as ApiClient;
    const controller = new PlaybackController(
      audio, visualizer, appState, new ClientStateMachine(appState), client, () => sink,
    );

    const speaking = controller.synthesize('Hello there.', 'message-1', 'chat-1', 'persona-1');
    controller.stop();

    expect(seen.aborted).toBe(true);
    await expect(speaking).resolves.toBeUndefined();
    // An interruption is not a reason to go and fetch the whole file instead.
    expect(client.synthesize).not.toHaveBeenCalled();
  });
});
