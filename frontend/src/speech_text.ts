/**
 * What a reply sounds like, as opposed to what it looks like.
 *
 * Two kinds of text are written but never meant to be said. A model's own
 * reasoning, inside `<think>`, is the persona thinking rather than talking. And
 * an aside acted out between asterisks - *smiles*, *laughs softly* - is a stage
 * direction: read aloud it becomes the persona narrating itself. Both are left
 * out of speech and stay on the screen.
 */

const THINK_BLOCK = /<think>[\s\S]*?<\/think>/gi;
const THINK_OPENING = /<think>/i;
// One asterisk each side, not two: `**bold**` is emphasis and is spoken.
const ASTERISK_SPAN = /\*(?![\s*])([^*\n]*?[^\s*])\*(?!\*)/g;
// Where an aside can stand on its own: the start of a line, or just after a
// sentence has ended. Inside a sentence, *really* is emphasis, not an action,
// and dropping it would change what was said.
const ASIDE_POSITION = /(?:^|\n)[ \t]*$|[.!?…]["'”’)]*[ \t]+$/;

/** Whether an asterisk span after this text is an aside rather than emphasis. Shared with how replies are shown. */
export function asideFollows(before: string): boolean {
  return ASIDE_POSITION.test(before);
}

/**
 * The part of `text` that can be spoken.
 *
 * While a reply is still arriving (`final` false) an aside or a reasoning
 * block that has opened but not yet closed holds back everything after it, so
 * no part of either is ever spoken while it is half-written. Once text has been
 * returned here it is never taken back: a later, longer `text` only ever adds
 * to the end, which is what lets a reply be spoken a sentence at a time.
 */
export function speakableText(text: string, final = false): string {
  let spoken = text.replace(THINK_BLOCK, '');
  // A reasoning block that never closes is reasoning to the end. It is never
  // spoken, finished or not.
  const openThink = spoken.search(THINK_OPENING);
  if (openThink !== -1) spoken = spoken.slice(0, openThink);
  spoken = spoken.replace(ASTERISK_SPAN, (match: string, _inner: string, offset: number, whole: string) =>
    ASIDE_POSITION.test(whole.slice(0, offset)) ? '' : match,
  );
  if (!final) {
    const openAside = unclosedAside(spoken);
    if (openAside !== -1) spoken = spoken.slice(0, openAside);
    // "<thi" may be the start of a reasoning block that has not finished
    // arriving; it is held back until it is either that or plainly not.
    spoken = spoken.slice(0, spoken.length - partialThinkTag(spoken));
  }
  return spoken;
}

/** How many characters at the end of `text` could be the start of `<think>`. */
function partialThinkTag(text: string): number {
  const tag = '<think>';
  for (let length = Math.min(tag.length - 1, text.length); length > 0; length -= 1) {
    if (text.slice(-length).toLowerCase() === tag.slice(0, length)) return length;
  }
  return 0;
}

/** Where an aside has opened on the line still being written, or -1. */
function unclosedAside(text: string): number {
  const lineStart = text.lastIndexOf('\n') + 1;
  for (let index = lineStart; index < text.length; index += 1) {
    if (text[index] !== '*' || text[index - 1] === '*' || text[index + 1] === '*') continue;
    const next = text[index + 1];
    if (next !== undefined && /\s/.test(next)) continue;
    if (ASIDE_POSITION.test(text.slice(0, index))) return index;
  }
  return -1;
}

export function speechText(text: string): string {
  return speakableText(text, true)
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]+\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/<[^>]+>/g, ' ')
    .replace(/[`*_>#~-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}
