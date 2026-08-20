import { describe, expect, it, vi } from 'vitest';

import {
  SETTINGS_GROUPS,
  SETTINGS_SECTIONS,
  searchSettings,
  sectionLabel,
} from '../src/settings';
import { settingsNav } from '../src/settings_nav';

describe('the settings map', () => {
  it('puts every section in exactly one group', () => {
    const placed = SETTINGS_GROUPS.flatMap((group) => group.sections);

    // The groups are a second listing of the sections, which is exactly the
    // kind of copy that drifts. This is what makes adding a section without
    // homing it a failing build instead of an unreachable page.
    expect([...placed].sort()).toEqual([...SETTINGS_SECTIONS].sort());
    expect(new Set(placed).size).toBe(placed.length);
  });

  it('speaks plainly where the section id is jargon', () => {
    expect(sectionLabel('TTS')).toBe('Spoken replies');
    expect(sectionLabel('STT')).toBe('Transcription');
    expect(sectionLabel('Task Models')).toBe('Background models');
    // A name that was already a word stays itself.
    expect(sectionLabel('Memory')).toBe('Memory');
  });
});

describe('searching the settings', () => {
  it('finds pages by the words a person actually thinks', () => {
    // None of these words appear in any section name - that is the point.
    expect(searchSettings('blur')).toContain('Image Generation');
    expect(searchSettings('microphone')).toContain('STT');
    expect(searchSettings('backup')).toContain('Data');
    expect(searchSettings('context')).toContain('Models');
    expect(searchSettings('lore')).toContain('Personas');
  });

  it('matches the friendly label and the id alike', () => {
    expect(searchSettings('spoken')).toContain('TTS');
    expect(searchSettings('tts')).toContain('TTS');
  });

  it('shows everything when nothing has been typed', () => {
    expect(searchSettings('')).toEqual([...SETTINGS_SECTIONS]);
    expect(searchSettings('   ')).toEqual([...SETTINGS_SECTIONS]);
  });
});

describe('the navigation', () => {
  function built(query = '') {
    const opened: string[] = [];
    const queries: string[] = [];
    const node = settingsNav({
      section: 'General',
      query,
      onQuery: (value) => queries.push(value),
      onOpen: (section) => opened.push(section),
    });
    return { node, opened, queries };
  }

  it('renders the groups with their pages under them', () => {
    const { node } = built();

    const titles = [...node.querySelectorAll('.settings-nav-group-title')].map((title) => title.textContent);
    expect(titles).toEqual(['Conversation', 'Voice', 'Pictures', 'Personas', 'System']);
    // The friendly label is what a person reads; the id stays in the testid so
    // every deep link and existing test still lands.
    const tts = node.querySelector('[data-testid="settings-nav-tts"]') as HTMLButtonElement;
    expect(tts.textContent).toBe('Spoken replies');
  });

  it('narrows to matching pages and drops empty groups', () => {
    const { node } = built('microphone');

    const titles = [...node.querySelectorAll('.settings-nav-group-title')].map((title) => title.textContent);
    expect(titles).toEqual(['Voice']);
    expect(node.querySelector('[data-testid="settings-nav-stt"]')).toBeTruthy();
    expect(node.querySelector('[data-testid="settings-nav-general"]')).toBeNull();
  });

  it('says plainly when nothing matches', () => {
    const { node } = built('zzzzz');

    expect(node.textContent).toContain('Nothing matches that.');
    expect(node.querySelector('.settings-nav-item')).toBeNull();
  });

  it('opens the page that was clicked', () => {
    const { node, opened } = built('whisper');

    (node.querySelector('[data-testid="settings-nav-stt"]') as HTMLButtonElement).click();

    expect(opened).toEqual(['STT']);
  });

  it('opens the best match on Enter', () => {
    const { node, opened } = built('backup');
    const input = node.querySelector('[data-testid="settings-search"]') as HTMLInputElement;

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));

    expect(opened).toEqual(['Data']);
  });

  it('reports what was typed so the caller can re-render', () => {
    const { node, queries } = built();
    const input = node.querySelector('[data-testid="settings-search"]') as HTMLInputElement;

    input.value = 'blur';
    input.dispatchEvent(new Event('input'));

    expect(queries).toEqual(['blur']);
  });
});
