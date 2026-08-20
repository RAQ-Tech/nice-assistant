import { el } from './dom';
import {
  SETTINGS_GROUPS,
  type SettingsSection,
  searchSettings,
  sectionLabel,
} from './settings';

/**
 * Five groups named for what somebody is trying to do, and a search box.
 *
 * Fifteen flat tabs asked a person to already know that the voice they hear is
 * "TTS" and the model that writes titles is a "Task Model". The groups carry
 * that knowledge instead, and search carries the rest: type the word you
 * actually think - "blur", "microphone", "backup" - and the list narrows to
 * the pages that answer it. Section ids are unchanged, so every deep link and
 * test that names one still works; only the wayfinding is new.
 */
export function settingsNav(options: {
  section: SettingsSection;
  query: string;
  onQuery: (value: string) => void;
  onOpen: (section: SettingsSection) => void;
}): HTMLElement {
  const matches = searchSettings(options.query);
  const groups = SETTINGS_GROUPS
    .map((group) => ({ ...group, sections: group.sections.filter((name) => matches.includes(name)) }))
    .filter((group) => group.sections.length);
  return el('aside', { class: 'settings-nav glass' }, [
    el('input', {
      class: 'settings-search',
      type: 'search',
      placeholder: 'Search settings',
      value: options.query,
      'aria-label': 'Search settings',
      'data-testid': 'settings-search',
      oninput: (event: Event) => options.onQuery((event.currentTarget as HTMLInputElement).value),
      // Enter opens the best match, so finding a setting is type-and-enter
      // rather than type, reach for the pointer, click.
      onkeydown: (event: Event) => {
        if ((event as KeyboardEvent).key !== 'Enter' || !matches.length) return;
        event.preventDefault();
        options.onOpen(matches[0] as SettingsSection);
      },
    }),
    el('div', { class: 'settings-nav-list' }, groups.length
      ? groups.map((group) =>
          el('div', { class: 'settings-nav-group' }, [
            el('div', { class: 'settings-nav-group-title', textContent: group.name }),
            ...group.sections.map((name) =>
              el('button', {
                class: `settings-nav-item ${name === options.section ? 'active' : ''}`,
                textContent: sectionLabel(name),
                'data-testid': `settings-nav-${slug(name)}`,
                onclick: () => options.onOpen(name),
              })),
          ]))
      : [el('p', { class: 'meta settings-search-empty', textContent: 'Nothing matches that.' })]),
  ]);
}

function slug(name: string): string {
  return name.toLowerCase().replace(/\s+/g, '-');
}
