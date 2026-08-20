/**
 * A face for a persona that has no picture, or whose picture will not load.
 *
 * The fallback used to be one generic silhouette everywhere - and on the new
 * dashboard, a path that did not even exist, so a persona without a picture
 * showed the browser's broken-image glyph. A persona is supposed to feel like
 * somebody; the least a missing picture can do is show who it is missing for.
 *
 * So: initials on a colored background, the way a phone's contacts do it. The
 * color comes from the name, deterministically, so April is always the same
 * shade on every screen and every device, and two personas rarely share one.
 */

const PALETTE = [
  ['#42e8ff', '#2b7fd9'],
  ['#a470ff', '#6d3fd4'],
  ['#ff7998', '#d9436e'],
  ['#ffb45e', '#e07f2e'],
  ['#5ee8a4', '#2eb877'],
  ['#7f9dff', '#4a63d9'],
  ['#ff8fd8', '#c957a5'],
  ['#e8d75e', '#b8a52e'],
] as const;

export function monogramInitials(name: string): string {
  const words = String(name || '')
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  const letters = words.slice(0, 2).map((word) => word[0]?.toUpperCase() ?? '');
  return letters.join('') || '?';
}

function paletteIndex(name: string): number {
  let hash = 0;
  for (const character of String(name || '')) hash = (hash * 31 + character.codePointAt(0)!) >>> 0;
  return hash % PALETTE.length;
}

/** A complete image the browser can show without asking anybody for anything. */
export function monogramAvatar(name: string): string {
  const [from, to] = PALETTE[paletteIndex(name)] as readonly [string, string];
  const initials = monogramInitials(name);
  const svg =
    `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 96 96'>` +
    `<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>` +
    `<stop stop-color='${from}'/><stop offset='1' stop-color='${to}'/>` +
    `</linearGradient></defs>` +
    `<rect width='96' height='96' fill='url(#g)'/>` +
    `<text x='48' y='48' dy='0.36em' text-anchor='middle' ` +
    `font-family='Inter, -apple-system, sans-serif' font-size='${initials.length > 1 ? 34 : 42}' ` +
    `font-weight='600' fill='rgba(255,255,255,0.94)'>${initials}</text>` +
    `</svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

/** What to put in an avatar's src: the picture if there is one, else the monogram. */
export function avatarSource(name: string, url: string | null | undefined): string {
  return (url || '').trim() || monogramAvatar(name);
}

/**
 * When the picture itself fails to arrive - the URL rotted, the service behind
 * it is down - the monogram takes its place instead of the broken-image glyph.
 * Once, so a monogram that somehow failed cannot loop.
 */
export function avatarErrorFallback(name: string): (event: Event) => void {
  return (event: Event) => {
    const image = event.currentTarget as HTMLImageElement;
    if (image.dataset.monogram) return;
    image.dataset.monogram = 'true';
    image.src = monogramAvatar(name);
  };
}
