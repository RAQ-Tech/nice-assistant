import type { ApiClient } from './api';
import type { PlaybackController } from './playback';
import { nextPieceEnd } from './sentence_boundaries';
import { speechText } from './speech_text';

/**
 * Speaking a reply while it is still being written.
 *
 * The chat hands over the visible text each time it grows. Every finished
 * sentence not yet spoken is queued, in order, and spoken into the one audio
 * stream the playback controller is already playing - so the first sound
 * arrives after the first sentence rather than after the last, and nothing is
 * ever spoken that is not already on the screen. When the reply is complete,
 * whatever is left is the last piece, and the server stores the whole reply
 * as one recording for replay. See ADR 0042.
 *
 * A stop, from anywhere, ends everything at once: the piece being spoken, the
 * pieces waiting, and the recording that would have been kept.
 */
export class ReplySpeaker {
  private queuedUntil = 0;
  private readonly queue: string[] = [];
  private session: { id: string; audioId: string } | null = null;
  private opening: Promise<{ id: string; audioId: string } | null> | null = null;
  private pumping: Promise<void> | null = null;
  private stopped = false;
  private spoke = false;

  constructor(
    private readonly playback: PlaybackController,
    private readonly client: ApiClient,
    private readonly chatId: string,
    private readonly personaId: string | null,
    private readonly format: string,
  ) {}

  /** Whether any piece reached the stream, so the caller knows not to speak the reply again. */
  get spokeAnything(): boolean {
    return this.spoke;
  }

  /** The visible text grew. Queue every finished sentence that is not yet queued. */
  observe(text: string): void {
    if (this.stopped) return;
    for (;;) {
      const end = nextPieceEnd(text, this.queuedUntil);
      if (end === -1) break;
      this.enqueue(text.slice(this.queuedUntil, end));
      this.queuedUntil = end;
    }
  }

  /** The reply is complete: the rest is the last piece, and the whole reply is stored. */
  async finish(text: string, messageId: string): Promise<void> {
    if (this.stopped) return;
    this.observe(text);
    this.enqueue(text.slice(this.queuedUntil));
    this.queuedUntil = text.length;
    await this.pumping;
    if (this.stopped) return;
    const session = this.session;
    if (!session || !this.spoke) {
      await this.playback.endPieces(null, messageId);
      return;
    }
    let audioUrl: string | null = null;
    try {
      const stored = await this.client.finishSpeechSession(session.id);
      audioUrl = `/api/v1/audio/${stored.audio_id}`;
    } catch {
      // The reply was heard; only the replay file is missing, and replay
      // will synthesize it again on demand.
    }
    await this.playback.endPieces(audioUrl, messageId);
  }

  /** Cut every piece, spoken or waiting, and keep nothing. */
  stop(): void {
    if (this.stopped) return;
    this.stopped = true;
    this.queue.length = 0;
    const session = this.session;
    if (session) void this.client.abandonSpeechSession(session.id).catch(() => undefined);
  }

  private enqueue(piece: string): void {
    const spoken = speechText(piece);
    if (!spoken) return;
    this.queue.push(spoken);
    this.pumping ??= this.pump().finally(() => { this.pumping = null; });
  }

  private async pump(): Promise<void> {
    while (this.queue.length && !this.stopped) {
      const piece = this.queue.shift() as string;
      const session = await this.ensureSession();
      if (!session || this.stopped) return;
      const played = await this.playback.appendPiece((signal) => this.client.streamSpeechPiece(session.id, piece, signal));
      if (!played) {
        // The stream is over - stopped by the person, or failed after it had
        // begun. Either way nothing more of this reply is spoken.
        this.stop();
        return;
      }
      this.spoke = true;
    }
  }

  private ensureSession(): Promise<{ id: string; audioId: string } | null> {
    if (this.session) return Promise.resolve(this.session);
    this.opening ??= this.client
      .beginSpeechSession({ chat_id: this.chatId, persona_id: this.personaId, format: this.format })
      .then((begun) => {
        this.session = { id: begun.session_id, audioId: begun.audio_id };
        return this.session;
      })
      .catch(() => {
        this.stop();
        return null;
      });
    return this.opening;
  }
}
