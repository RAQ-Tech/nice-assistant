import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { PlaybackController } from '../src/playback';
import { ClientStateMachine, createState } from '../src/state';
import type { AudioStreamSink } from '../src/streaming_audio';
import type { Settings } from '../src/types';
import type { Visualizer } from '../src/visualization';

/**
 * One reply, spoken in pieces into the one stream (ADR 0042): the element is
 * given the growing source at the first piece, every later piece is appended
 * behind it, a stop closes it all, and the stored whole is what replay uses.
 */

class RecordingSink implements AudioStreamSink {
  readonly pieces: Uint8Array[] = [];
  opened = 0;
  ended = false;
  closed = false;
  async open(): Promise<string> {
    this.opened += 1;
    return 'blob:pieces';
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

function responseOf(pieces: string[]): Response {
  return new Response(new ReadableStream<Uint8Array>({
    start(controller) {
      pieces.forEach((piece) => controller.enqueue(new TextEncoder().encode(piece)));
      controller.close();
    },
  }));
}

function setup(format = 'mp3', phase: 'idle' | 'thinking' = 'thinking') {
  const appState = createState();
  appState.phase = phase;
  appState.voiceResponsesEnabled = true;
  appState.settings = { tts_provider: 'local', tts_format: format } as Settings;
  const audio = document.createElement('audio');
  const play = vi.spyOn(audio, 'play').mockResolvedValue(undefined);
  vi.spyOn(audio, 'pause').mockImplementation(() => undefined);
  const visualizer = { connectAudio: vi.fn() } as unknown as Visualizer;
  const sink = new RecordingSink();
  const controller = new PlaybackController(audio, visualizer, appState, new ClientStateMachine(appState), {} as ApiClient, () => sink);
  return { appState, audio, play, sink, controller };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('a reply spoken in pieces', () => {
  it('feeds every piece into the one stream and starts playing at the first', async () => {
    vi.stubGlobal('MediaSource', { isTypeSupported: () => true });
    const { appState, audio, play, sink, controller } = setup();

    expect(controller.beginPieces('typing-1')).toBe(true);
    expect(await controller.appendPiece(async () => responseOf(['one', 'two']))).toBe(true);
    expect(await controller.appendPiece(async () => responseOf(['three']))).toBe(true);

    expect(sink.opened).toBe(1);
    expect(sink.pieces.map((piece) => new TextDecoder().decode(piece))).toEqual(['one', 'two', 'three']);
    expect(audio.src).toBe('blob:pieces');
    expect(play).toHaveBeenCalledTimes(1);
    // Speaking began while the reply was still being written.
    await vi.waitFor(() => expect(appState.phase).toBe('speaking'));

    await controller.endPieces('/api/v1/audio/audio-1', 'assistant-1');
    expect(sink.ended).toBe(true);
    expect(appState.messageAudioById['assistant-1']).toBe('/api/v1/audio/audio-1');
    expect(appState.currentAudioMessageId).toBe('assistant-1');
  });

  it('refuses a format that cannot start early, so the completed reply is spoken instead', () => {
    vi.stubGlobal('MediaSource', { isTypeSupported: () => true });
    const { controller } = setup('wav');
    expect(controller.beginPieces('typing-1')).toBe(false);
  });

  it('a stop closes the stream and refuses every later piece', async () => {
    vi.stubGlobal('MediaSource', { isTypeSupported: () => true });
    const { sink, controller } = setup();
    controller.beginPieces('typing-1');
    await controller.appendPiece(async () => responseOf(['one']));

    controller.stop();

    expect(sink.closed).toBe(true);
    expect(await controller.appendPiece(async () => responseOf(['two']))).toBe(false);
    expect(sink.pieces).toHaveLength(1);
  });

  it('a stop in the middle of a piece drops the rest of it', async () => {
    vi.stubGlobal('MediaSource', { isTypeSupported: () => true });
    const { sink, controller } = setup();
    controller.beginPieces('typing-1');
    let releaseSecond!: () => void;
    const body = new ReadableStream<Uint8Array>({
      async start(streamController) {
        streamController.enqueue(new TextEncoder().encode('one'));
        await new Promise<void>((resolve) => { releaseSecond = resolve; });
        streamController.enqueue(new TextEncoder().encode('two'));
        streamController.close();
      },
    });
    const pending = controller.appendPiece(async () => new Response(body));
    await vi.waitFor(() => expect(sink.pieces).toHaveLength(1));
    controller.stop();
    releaseSecond();

    expect(await pending).toBe(false);
    expect(sink.pieces).toHaveLength(1);
  });
});
