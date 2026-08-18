/**
 * The pieces of one spoken turn, kept in the order they were said.
 *
 * A long answer is cut at its pauses so the earlier parts can be transcribed
 * while the later parts are still being spoken. The pieces then come back in
 * whatever order the transcription service finishes them, which is not the
 * order somebody said them - so each piece claims its place before the work
 * starts, and fills it in later.
 *
 * A piece that fails leaves a hole rather than failing the turn. The rest of
 * what somebody said is still worth having, and an error over a sentence that
 * mostly arrived would be worse than the gap.
 */
export class TranscriptSegments {
  private pieces: string[] = [];
  private pending: Promise<void>[] = [];
  private claimed = 0;

  begin(): void {
    this.pieces = [];
    this.pending = [];
    this.claimed = 0;
  }

  /** Reserve this piece's place in the sentence, then go and transcribe it. */
  claim(work: () => Promise<string>, onUpdate: (joined: string) => void): void {
    const index = this.claimed;
    this.claimed += 1;
    this.pending.push(
      work()
        .then((text) => { this.pieces[index] = text.trim(); })
        .catch(() => { this.pieces[index] = ''; })
        .then(() => { onUpdate(this.joined()); }),
    );
  }

  /**
   * Add the last piece - the only one anybody actually waited for - and give
   * back the whole turn once every earlier piece has landed.
   */
  async finish(tail: string): Promise<string> {
    this.pieces[this.claimed] = tail.trim();
    await Promise.all(this.pending);
    return this.joined();
  }

  joined(): string {
    return this.pieces.filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
  }

  /** Whether anything has been claimed or transcribed for this turn yet. */
  get empty(): boolean {
    return this.claimed === 0 && !this.joined();
  }
}
