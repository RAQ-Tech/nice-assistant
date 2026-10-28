import { describe, expect, it } from 'vitest';

import { markdown } from '../src/markdown';

describe('replies as formatted text', () => {
  it('shows emphasis rather than the marks around it', () => {
    expect(markdown('That was **brilliant** and *really* kind.')).toBe(
      '<p>That was <strong>brilliant</strong> and <em>really</em> kind.</p>',
    );
  });

  it('shows an acted-out aside as an aside, not as asterisks', () => {
    expect(markdown('*smiles warmly* Hello. *laughs* Really.')).toBe(
      '<p><em class="md-aside">smiles warmly</em> Hello. <em class="md-aside">laughs</em> Really.</p>',
    );
  });

  it('keeps two lists apart and the paragraph between them', () => {
    expect(markdown('First:\n- one\n- two\n\nThen:\n1. three\n2. four')).toBe(
      '<p>First:</p><ul><li>one</li><li>two</li></ul><p>Then:</p><ol><li>three</li><li>four</li></ol>',
    );
  });

  it('formats headings, quotes and code without letting markup through', () => {
    expect(markdown('## Plan\n> said before\n```python\nprint("<b>")\n```')).toBe(
      '<p class="md-heading"><strong>Plan</strong></p><blockquote>said before</blockquote><pre><code>print(&quot;&lt;b&gt;&quot;)</code></pre>',
    );
  });

  it('loads a picture only from this server', () => {
    expect(markdown('![me](/api/v1/media/one)')).toContain('<img src="/api/v1/media/one"');
    // Fetching it would tell another host somebody was reading this.
    const elsewhere = markdown('![tracker](https://example.test/pixel.png)');
    expect(elsewhere).not.toContain('<img');
    expect(elsewhere).toContain('href="https://example.test/pixel.png"');
    expect(markdown('![x](//example.test/a.png)')).not.toContain('<img');
  });

  it('never lets the model write HTML', () => {
    expect(markdown('<img src=x onerror=alert(1)> *hi*')).not.toContain('<img');
  });
});
