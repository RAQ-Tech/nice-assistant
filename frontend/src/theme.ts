/**
 * Light, dark, or whatever the device is set to.
 *
 * The stylesheet knows two themes; "system" is resolved here, so there is one
 * light block to maintain rather than a second copy inside a media query. The
 * choice follows the device as it changes - a phone that goes dark at sunset
 * takes the app with it.
 *
 * The last choice is remembered in this browser so the first paint, before the
 * settings have loaded, is already the right one. It is a display preference,
 * not anything about the account.
 */

export type ThemeChoice = 'system' | 'light' | 'dark';
export const THEME_CHOICES: ThemeChoice[] = ['system', 'light', 'dark'];

const REMEMBERED = 'nice-assistant-theme';
const LIGHT_QUERY = '(prefers-color-scheme: light)';

let choice: ThemeChoice = 'system';
let listening = false;

function asChoice(value: unknown): ThemeChoice {
  return THEME_CHOICES.includes(value as ThemeChoice) ? (value as ThemeChoice) : 'system';
}

function systemIsLight(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia(LIGHT_QUERY).matches;
}

function paint(): void {
  const resolved = choice === 'system' ? (systemIsLight() ? 'light' : 'dark') : choice;
  const root = document.documentElement;
  root.dataset.theme = resolved;
  // The installed app's status bar, and the browser's toolbar, match the page.
  const background = getComputedStyle(root).getPropertyValue('--bg').trim();
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (meta && background) meta.content = background;
}

export function applyTheme(value: unknown): void {
  choice = asChoice(value);
  try {
    localStorage.setItem(REMEMBERED, choice);
  } catch {
    // Storage can be refused; the theme still applies for this visit.
  }
  if (!listening && typeof window.matchMedia === 'function') {
    listening = true;
    window.matchMedia(LIGHT_QUERY).addEventListener('change', () => {
      if (choice === 'system') paint();
    });
  }
  paint();
}

/** Before anything is known about the account: the last choice made here. */
export function applyRememberedTheme(): void {
  let remembered: string | null = null;
  try {
    remembered = localStorage.getItem(REMEMBERED);
  } catch {
    remembered = null;
  }
  applyTheme(remembered);
}

export function themeLabel(value: string): string {
  return value === 'system' ? 'Match my device' : value === 'light' ? 'Light' : 'Dark';
}
