/**
 * Playing audio while it is still arriving.
 *
 * An `<audio>` element given a completed file cannot start until the file
 * exists. Media Source Extensions let one be fed piece by piece instead, which
 * is what turns "wait for the whole reply to be spoken" into "start speaking".
 *
 * Only some formats can be fed this way. WAV carries its length in a header
 * nothing can fill in halfway through, so a WAV reply cannot start early no
 * matter how it is delivered - the completed-file path stays for those.
 */

export interface AudioStreamSink {
  /** A URL the audio element can play while pieces are still being added. */
  open(mimeType: string): Promise<string>;
  append(chunk: Uint8Array): Promise<void>;
  /** No more pieces are coming. */
  end(): Promise<void>;
  /** Give up on this stream and release what it held. */
  close(): void;
}

const STREAMABLE_MIME_TYPES: Record<string, string> = {
  mp3: 'audio/mpeg',
  aac: 'audio/aac',
};

/**
 * The mime type this format can be streamed as, or null if it cannot be.
 *
 * Null covers both "this format has no incremental representation" and "this
 * browser will not play that one", and the caller treats them the same: fall
 * back to the completed file rather than fail.
 */
export function streamableMimeType(format: string): string | null {
  const mime = STREAMABLE_MIME_TYPES[String(format || '').toLowerCase()];
  if (!mime) return null;
  const media = (globalThis as { MediaSource?: { isTypeSupported?(type: string): boolean } }).MediaSource;
  return media?.isTypeSupported?.(mime) ? mime : null;
}

export class MediaSourceSink implements AudioStreamSink {
  private source: MediaSource | null = null;
  private buffer: SourceBuffer | null = null;
  private url = '';

  async open(mimeType: string): Promise<string> {
    const source = new MediaSource();
    this.source = source;
    this.url = URL.createObjectURL(source);
    await new Promise<void>((resolve) => {
      source.addEventListener('sourceopen', () => resolve(), { once: true });
    });
    this.buffer = source.addSourceBuffer(mimeType);
    return this.url;
  }

  async append(chunk: Uint8Array): Promise<void> {
    const buffer = this.buffer;
    if (!buffer) return;
    // A source buffer takes one append at a time, so each piece waits for the
    // one before it rather than throwing.
    await this.settled(buffer);
    buffer.appendBuffer(chunk as unknown as BufferSource);
    await this.settled(buffer);
  }

  async end(): Promise<void> {
    const source = this.source;
    if (!source || source.readyState !== 'open') return;
    if (this.buffer) await this.settled(this.buffer);
    source.endOfStream();
  }

  close(): void {
    // The element keeps playing whatever it has already buffered; this only
    // releases the handle, so revoking the URL cannot cut a reply short.
    if (this.url) URL.revokeObjectURL(this.url);
    this.url = '';
    this.buffer = null;
    this.source = null;
  }

  private settled(buffer: SourceBuffer): Promise<void> {
    if (!buffer.updating) return Promise.resolve();
    return new Promise<void>((resolve) => {
      buffer.addEventListener('updateend', () => resolve(), { once: true });
    });
  }
}
