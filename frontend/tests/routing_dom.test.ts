import { describe, expect, it } from 'vitest';

import { markdown } from '../src/dom';
import { parseRoute } from '../src/routing';

describe('routing and safe rendering', () => {
  it('parses stable chat and settings routes', () => {
    expect(parseRoute('#/chats/chat%201')).toEqual({ kind: 'chat', chatId: 'chat 1' });
    expect(parseRoute('#/settings/Memory')).toEqual({ kind: 'settings', section: 'Memory' });
    expect(parseRoute('')).toEqual({ kind: 'home' });
  });

  it('escapes model HTML before applying limited markdown', () => {
    const output = markdown('<script>alert(1)</script> **safe**');
    expect(output).toContain('&lt;script&gt;');
    expect(output).not.toContain('<script>');
  });
});
