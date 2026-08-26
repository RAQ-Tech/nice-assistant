import { describe, expect, it } from 'vitest';

import { captureScroll, captureScrollPositions, markdown, restoreScroll, restoreScrollPositions } from '../src/dom';
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

  it('puts every scrolled pane back after a full re-render, not just one', () => {
    // The settings screen: a nav pane and a detail pane, each scrolled to a
    // different place. Clicking one checkbox rebuilds both, and both used to
    // land back at the top.
    const page = `
      <aside class="settings-nav glass"><div class="settings-nav-list"></div></aside>
      <section class="settings-detail glass"></section>
      <div class="home"></div>
    `;
    document.body.innerHTML = page;
    const nav = document.querySelector<HTMLElement>('.settings-nav-list') as HTMLElement;
    const detail = document.querySelector<HTMLElement>('.settings-detail') as HTMLElement;
    const home = document.querySelector<HTMLElement>('.home') as HTMLElement;
    nav.scrollTop = 120;
    detail.scrollTop = 340;
    home.scrollTop = 900;

    const captured = captureScrollPositions(document);
    document.body.innerHTML = page;
    restoreScrollPositions(document, captured);

    expect(document.querySelector<HTMLElement>('.settings-nav-list')?.scrollTop).toBe(120);
    expect(document.querySelector<HTMLElement>('.settings-detail')?.scrollTop).toBe(340);
    expect(document.querySelector<HTMLElement>('.home')?.scrollTop).toBe(900);
  });

  it('tells identical-looking panes apart by their position', () => {
    const page = '<div class="card"></div><div class="card"></div><div class="card"></div>';
    document.body.innerHTML = page;
    const cards = document.querySelectorAll<HTMLElement>('.card');
    (cards[1] as HTMLElement).scrollTop = 55;
    (cards[2] as HTMLElement).scrollLeft = 40;

    const captured = captureScrollPositions(document);
    document.body.innerHTML = page;
    restoreScrollPositions(document, captured);

    const rebuilt = document.querySelectorAll<HTMLElement>('.card');
    expect(rebuilt[0]?.scrollTop).toBe(0);
    expect(rebuilt[1]?.scrollTop).toBe(55);
    expect(rebuilt[2]?.scrollLeft).toBe(40);
  });

  it('starts at the top when a pane is showing different content', () => {
    // Switching settings sections rebuilds the same-looking detail pane around
    // new content. That is navigation, not a refresh: the new section starts
    // at the top, while the section list beside it keeps its place.
    const before = `
      <aside class="settings-nav-list"></aside>
      <section class="settings-detail glass"><h3>General</h3><p>rows</p></section>
    `;
    document.body.innerHTML = before;
    (document.querySelector<HTMLElement>('.settings-nav-list') as HTMLElement).scrollTop = 60;
    (document.querySelector<HTMLElement>('.settings-detail') as HTMLElement).scrollTop = 480;

    const captured = captureScrollPositions(document);
    document.body.innerHTML = `
      <aside class="settings-nav-list"></aside>
      <section class="settings-detail glass"><h3>Memory</h3><p>rows</p></section>
    `;
    restoreScrollPositions(document, captured);

    expect(document.querySelector<HTMLElement>('.settings-nav-list')?.scrollTop).toBe(60);
    expect(document.querySelector<HTMLElement>('.settings-detail')?.scrollTop).toBe(0);
  });

  it('skips panes that no longer exist and never throws', () => {
    document.body.innerHTML = '<div class="gone"></div>';
    (document.querySelector<HTMLElement>('.gone') as HTMLElement).scrollTop = 75;

    const captured = captureScrollPositions(document);
    document.body.innerHTML = '<div class="different"></div>';

    expect(() => restoreScrollPositions(document, captured)).not.toThrow();
    expect(document.querySelector<HTMLElement>('.different')?.scrollTop).toBe(0);
    // Nothing scrolled means nothing captured, and restore is a no-op.
    expect(captureScrollPositions(document)).toEqual([]);
  });
});
