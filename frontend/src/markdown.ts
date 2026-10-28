import { escapeHtml } from './dom';
import { asideFollows } from './speech_text';

/**
 * Replies as formatted text, with the model's own HTML never let through.
 *
 * Everything is escaped first and only then given the little formatting a
 * reply uses: emphasis, lists, quotes, code, links, and pictures. An aside
 * acted out between asterisks at the start of a line or sentence - *smiles* -
 * is shown as one rather than as asterisks, by the same rule that keeps it out
 * of speech. A picture is only ever loaded from this server: a reply naming an
 * address elsewhere would otherwise make the browser fetch it, telling that
 * host somebody was reading.
 */

const CACHE_LIMIT = 400;
const rendered = new Map<string, string>();
const OWN_SERVER = /^\/(?!\/)/;
const ASTERISK_SPAN = /\*(?![\s*])([^*\n]*?[^\s*])\*(?!\*)/g;

export function markdown(text = ''): string {
  // Every redraw renders every message; a finished one renders the same each
  // time, so it is only worked out once.
  const cached = rendered.get(text);
  if (cached !== undefined) return cached;
  const html = blocks(text);
  if (rendered.size >= CACHE_LIMIT) rendered.delete(rendered.keys().next().value as string);
  rendered.set(text, html);
  return html;
}

function blocks(text: string): string {
  const fences: string[] = [];
  const source = text.replace(/```(?:[\w+-]+\n)?([\s\S]*?)```/g, (_match, code: string) => {
    fences.push(`<pre><code>${escapeHtml(code.trim())}</code></pre>`);
    return `\u0000F${fences.length - 1}\u0000`;
  });
  const out: string[] = [];
  let paragraph: string[] = [];
  let quote: string[] = [];
  let list: { tag: 'ul' | 'ol'; items: string[] } | null = null;
  const flush = (): void => {
    if (paragraph.length) out.push(`<p>${paragraph.map((line) => inline(line, fences)).join('<br/>')}</p>`);
    if (quote.length) out.push(`<blockquote>${quote.map((line) => inline(line, fences)).join('<br/>')}</blockquote>`);
    if (list) out.push(`<${list.tag}>${list.items.map((item) => `<li>${inline(item, fences)}</li>`).join('')}</${list.tag}>`);
    paragraph = [];
    quote = [];
    list = null;
  };
  for (const line of escapeHtml(source).split('\n')) {
    const fence = /^\u0000F(\d+)\u0000$/.exec(line.trim());
    const heading = /^#{1,6}\s+(.+)$/.exec(line);
    const item = /^\s*(?:([-*+])|\d+[.)])\s+(.+)$/.exec(line);
    const quoted = /^&gt;\s?(.*)$/.exec(line);
    if (fence || heading || !line.trim()) {
      flush();
      if (fence) out.push(fences[Number(fence[1])] ?? '');
      if (heading) out.push(`<p class="md-heading"><strong>${inline(heading[1] ?? '', fences)}</strong></p>`);
    } else if (item) {
      const tag = item[1] ? 'ul' : 'ol';
      if (paragraph.length || quote.length || (list && list.tag !== tag)) flush();
      list ??= { tag, items: [] };
      list.items.push(item[2] ?? '');
    } else if (quoted) {
      if (paragraph.length || list) flush();
      quote.push(quoted[1] ?? '');
    } else {
      if (quote.length || list) flush();
      paragraph.push(line);
    }
  }
  flush();
  return out.join('');
}

/** Formatting within one line, which has already been escaped. */
function inline(value: string, fences: string[]): string {
  const codes: string[] = [];
  return value
    .replace(/`([^`]+)`/g, (_match, code: string) => {
      codes.push(`<code>${code}</code>`);
      return `\u0000C${codes.length - 1}\u0000`;
    })
    .replace(/!\[([^\]]*)\]\(([^\s)]+)\)/g, (_match, alt: string, src: string) => {
      if (OWN_SERVER.test(src)) return `<img src="${src}" alt="${alt}" class="msg-inline-image" loading="lazy" />`;
      return /^https?:\/\//.test(src) ? `<a href="${src}" target="_blank" rel="noreferrer noopener">${alt || 'picture'}</a>` : alt;
    })
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>')
    .replace(/\*\*([^*\n]+?)\*\*|__([^_\n]+?)__/g, (_match, stars?: string, bars?: string) => `<strong>${stars ?? bars}</strong>`)
    .replace(ASTERISK_SPAN, (_match, words: string, offset: number, whole: string) =>
      asideFollows(whole.slice(0, offset)) ? `<em class="md-aside">${words}</em>` : `<em>${words}</em>`,
    )
    .replace(/(^|[^\w])_([^_\n]+?)_(?!\w)/g, '$1<em>$2</em>')
    .replace(/\u0000C(\d+)\u0000/g, (_match, index: string) => codes[Number(index)] ?? '')
    .replace(/\u0000F(\d+)\u0000/g, (_match, index: string) => fences[Number(index)] ?? '');
}
