import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import type { PlaybackController } from '../src/playback';
import { ReplySpeaker } from '../src/reply_speaker';
import { MIN_PIECE_CHARS, nextPieceEnd } from '../src/sentence_boundaries';

/**
 * Speaking while the reply is written (ADR 0042): every finished sentence is
 * spoken as it appears, the sound never passes the text, a stop cuts every
 * queued piece, and the whole reply is stored once.
 */

describe('where a reply can be spoken from', () => {
  it('ends a piece at terminal punctuation followed by a break, once it is long enough', () => {
    const text = 'The first sentence is long enough to say. The second one is still being';
    const end = nextPieceEnd(text, 0);
    expect(text.slice(0, end)).toBe('The first sentence is long enough to say.');
    // The rest has no ending yet: the writer may still be adding to it.
    expect(nextPieceEnd(text, end)).toBe(-1);
  });

  it('does not treat a full stop at the very end of the text as an ending', () => {
    expect(nextPieceEnd('A sentence that just finished, or did it.', 0)).toBe(-1);
    expect(nextPieceEnd('A sentence that just finished, or did it. ', 0)).toBeGreaterThan(0);
  });

  it('waits for enough words rather than speaking a stutter', () => {
    const text = 'Yes. It is, and here is the longer explanation of why that is so. Next';
    const end = nextPieceEnd(text, 0);
    expect(end).toBeGreaterThan(MIN_PIECE_CHARS);
    expect(text.slice(0, end)).toBe('Yes. It is, and here is the longer explanation of why that is so.');
  });

  it('takes a paragraph end as an ending even without punctuation', () => {
    const text = 'Here is a heading line that is long enough\n\nAnd the paragraph after it';
    expect(text.slice(0, nextPieceEnd(text, 0))).toBe('Here is a heading line that is long enough');
  });

  it('never cuts inside a code fence', () => {
    const text = 'Run this, and read the output carefully. ```\nprint("hello. world")\n``` Then continue with the rest of it. ';
    const first = nextPieceEnd(text, 0);
    expect(text.slice(0, first)).toBe('Run this, and read the output carefully.');
    const second = nextPieceEnd(text, first);
    expect(text.slice(first, second).trim().endsWith('Then continue with the rest of it.')).toBe(true);
  });
});

function fakes() {
  const spoken: string[] = [];
  const responses: Response[] = [];
  const playback = {
    appendPiece: vi.fn(async (fetchPiece: (signal: AbortSignal) => Promise<Response>) => {
      responses.push(await fetchPiece(new AbortController().signal));
      return true;
    }),
    endPieces: vi.fn(async () => undefined),
  } as unknown as PlaybackController;
  const client = {
    beginSpeechSession: vi.fn().mockResolvedValue({ session_id: 'session-1', audio_id: 'audio-1', format: 'mp3' }),
    streamSpeechPiece: vi.fn(async (_session: string, text: string) => {
      spoken.push(text);
      return new Response(new Blob(['x']));
    }),
    finishSpeechSession: vi.fn().mockResolvedValue({ audio_id: 'audio-1', format: 'mp3' }),
    abandonSpeechSession: vi.fn().mockResolvedValue({ ok: true }),
  } as unknown as ApiClient;
  const speaker = new ReplySpeaker(playback, client, 'chat-1', 'persona-1', 'mp3');
  return { spoken, playback, client, speaker };
}

describe('speaking a reply as it is written', () => {
  it('speaks each finished sentence as it appears, never past the visible text', async () => {
    const { spoken, client, speaker } = fakes();
    speaker.observe('Here is the first sentence, which is');
    speaker.observe('Here is the first sentence, which is long enough. And the second');
    await vi.waitFor(() => expect(spoken).toEqual(['Here is the first sentence, which is long enough.']));
    // Nothing of the second sentence is spoken while it is still being written.
    expect(client.streamSpeechPiece).toHaveBeenCalledTimes(1);
    expect(client.beginSpeechSession).toHaveBeenCalledWith({ chat_id: 'chat-1', persona_id: 'persona-1', format: 'mp3' });
  });

  it('speaks the remainder when the reply is complete, and stores the whole reply once', async () => {
    const { spoken, client, playback, speaker } = fakes();
    speaker.observe('Here is the first sentence, which is long enough. And the second');
    await speaker.finish('Here is the first sentence, which is long enough. And the second, short.', 'assistant-1');

    expect(spoken).toEqual(['Here is the first sentence, which is long enough.', 'And the second, short.']);
    expect(client.finishSpeechSession).toHaveBeenCalledWith('session-1');
    expect(playback.endPieces).toHaveBeenCalledWith('/api/v1/audio/audio-1', 'assistant-1');
    expect(speaker.spokeAnything).toBe(true);
  });

  it('a stop cuts every queued piece and keeps no recording', async () => {
    const { spoken, client, playback, speaker } = fakes();
    let release!: () => void;
    (playback.appendPiece as ReturnType<typeof vi.fn>).mockImplementationOnce(
      () => new Promise<boolean>((resolve) => { release = () => resolve(false); }),
    );
    speaker.observe('The first sentence is being spoken right now. The second is waiting behind it. ');
    // The first piece is mid-stream when the stop comes.
    await vi.waitFor(() => expect(playback.appendPiece).toHaveBeenCalled());
    speaker.stop();
    release();
    await speaker.finish('The first sentence is being spoken right now. The second is waiting behind it. Third.', 'assistant-1');

    expect(spoken).toEqual([]);
    expect(client.abandonSpeechSession).toHaveBeenCalledWith('session-1');
    expect(client.finishSpeechSession).not.toHaveBeenCalled();
    expect(playback.endPieces).not.toHaveBeenCalled();
  });

  it('a reply with nothing to say ends the stream without a recording', async () => {
    const { client, playback, speaker } = fakes();
    await speaker.finish('```\ncode only\n```', 'assistant-1');

    expect(client.beginSpeechSession).not.toHaveBeenCalled();
    expect(playback.endPieces).toHaveBeenCalledWith(null, 'assistant-1');
    expect(speaker.spokeAnything).toBe(false);
  });
});
