import { describe, expect, it } from 'vitest';

import { captureScroll, markdown, restoreScroll } from '../src/dom';
import { parseRoute } from '../src/routing';

describe('routing and safe rendering', () => {
  it('parses stable chat and settings routes', () => {
    expect(parseRoute('#/chats/chat%201')).toEqual({ kind: 'chat', chatId: 'chat 1' });
    expect(parseRoute('#/settings/Memory')).toEqual({ kind: 'settings', section: 'Memory' });
    expect(parseRoute('')).toEqual({ kind: 'home' });
  });

  it('puts a scrolled reader back where they were after a re-render', () => {
    document.body.innerHTML = '<div id="pane"></div>';
    const pane = document.querySelector<HTMLElement>('#pane') as HTMLElement;
    // jsdom does not lay out, so scrollTop is whatever it is set to. That is
    // enough: what is under test is that the value survives the rebuild.
    pane.scrollTop = 420;

    const captured = captureScroll('#pane');
    document.body.innerHTML = '<div id="pane"></div>';
    restoreScroll('#pane', captured);

    // Rendering replaces the whole tree, so a rebuilt pane starts at the top.
    // Being yanked there three times a second while a picture generates is
    // what this exists to stop.
    expect(captured).toBe(420);
    expect(document.querySelector<HTMLElement>('#pane')?.scrollTop).toBe(420);
  });

  it('does not force a position on a pane that was already at the top', () => {
    document.body.innerHTML = '<div id="pane"></div>';

    expect(captureScroll('#pane')).toBe(0);
    expect(captureScroll('#missing')).toBeNull();
    // Neither is a position worth restoring, and neither should throw.
    restoreScroll('#pane', 0);
    restoreScroll('#missing', 99);
    expect(document.querySelector<HTMLElement>('#pane')?.scrollTop).toBe(0);
  });

  it('escapes model HTML before applying limited markdown', () => {
    const output = markdown('<script>alert(1)</script> **safe**');
    expect(output).toContain('&lt;script&gt;');
    expect(output).not.toContain('<script>');
  });
});
