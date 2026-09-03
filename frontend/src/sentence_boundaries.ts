/**
 * Where a reply can be spoken from, while the rest is still being written.
 *
 * ADR 0037 kept the product out of the business of deciding where sentences
 * end, because it would get that wrong on exactly the replies people care
 * about. ADR 0042 makes that decision anyway, conservatively, because the
 * alternative is waiting for the whole reply before a word is heard. The rule
 * is deliberately narrow: a sentence ends at terminal punctuation followed by
 * a break - whitespace or the end of a paragraph - and only once the piece is
 * long enough that speaking it alone will not sound like a stutter. Anything
 * doubtful waits; a doubtful cut costs a bad seam, a late cut costs a moment.
 */

const TERMINAL = /[.!?…]["'”’)]*$/;
export const MIN_PIECE_CHARS = 24;

/**
 * The end (exclusive) of the next speakable piece that starts at `from`, or
 * -1 when the text after `from` holds no finished sentence yet.
 *
 * Only text before a break is considered finished: the writer may still be
 * adding to the last line, so a full stop at the very end of the text is not
 * yet an ending. Code fences are never cut into; speaking code is not reading.
 */
export function nextPieceEnd(text: string, from: number, minChars = MIN_PIECE_CHARS): number {
  let fences = 0;
  let cursor = from;
  while (cursor < text.length) {
    const nextBreak = findBreak(text, cursor);
    if (nextBreak === -1) return -1;
    const candidate = text.slice(from, nextBreak);
    fences += countFences(text.slice(cursor, nextBreak));
    cursor = nextBreak + 1;
    if (fences % 2 === 1) continue;
    const trimmed = candidate.trim();
    if (trimmed.length < minChars) continue;
    if (!TERMINAL.test(trimmed) && !isParagraphEnd(text, nextBreak)) continue;
    return nextBreak;
  }
  return -1;
}

function findBreak(text: string, from: number): number {
  for (let index = from; index < text.length; index += 1) {
    const character = text[index];
    if (character === '\n' || character === ' ' || character === '\t') return index;
  }
  return -1;
}

function isParagraphEnd(text: string, at: number): boolean {
  return text[at] === '\n' && text[at + 1] === '\n';
}

function countFences(text: string): number {
  return (text.match(/```/g) ?? []).length;
}
