import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { parseRoute } from '../src/routing';
import { normalizeSettings, settingsWire } from '../src/settings';
import { SettingsView, type Dialogs } from '../src/settings_view';
import { createState } from '../src/state';
import type { SettingsWire } from '../src/types';

function wire(): SettingsWire {
  return {
    global_default_model: null,
    default_memory_mode: 'saved',
    stt_provider: 'disabled',
    tts_provider: 'local',
    tts_format: 'mp3',
    openai_api_key: null,
    onboarding_done: true,
    preferences: { general_theme: 'system' },
  };
}

function setup(updateSettings = vi.fn().mockImplementation((body: SettingsWire) => Promise.resolve(body))) {
  const appState = createState();
  appState.settings = normalizeSettings(wire());
  appState.settingsSection = 'General';
  const confirm = vi.fn().mockResolvedValue(true);
  const dialogs = { prompt: vi.fn(), confirm, info: vi.fn(), choice: vi.fn() } as unknown as Dialogs;
  const close = vi.fn();
  const view = new SettingsView(vi.fn(), close, dialogs, appState, { updateSettings } as unknown as ApiClient);
  return { appState, view, updateSettings, confirm, close };
}

function choose(node: HTMLElement, label: string, value: string): void {
  const row = [...node.querySelectorAll('.setting-row')].find((item) => item.textContent?.startsWith(label));
  const select = row?.querySelector('select') as HTMLSelectElement;
  select.value = value;
  select.dispatchEvent(new Event('change'));
}

describe('everyday settings save themselves', () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it('saves a change after a short pause, with no Save button to forget', async () => {
    const { appState, view, updateSettings } = setup();
    const node = view.node();
    // The header button this replaced is why a theme could be chosen, look
    // applied, and be gone after a reload.
    expect(node.querySelector('[data-testid="settings-save"]')).toBeNull();

    choose(node, 'Theme', 'dark');
    expect(view.node().querySelector('[data-testid="settings-save-status"]')?.textContent).toBe('Saving…');
    expect(updateSettings).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(700);
    expect(updateSettings).toHaveBeenCalledTimes(1);
    expect(updateSettings.mock.calls[0]?.[0].preferences.general_theme).toBe('dark');
    expect(appState.settingsSavedAt).toBeGreaterThan(0);
    expect(view.node().querySelector('[data-testid="settings-save-status"]')?.textContent).toBe('Saved');
  });

  it('keeps what was typed during a save, and saves it straight after', async () => {
    let answer: (value: SettingsWire) => void = () => undefined;
    const updateSettings = vi.fn()
      .mockImplementationOnce(() => new Promise<SettingsWire>((resolve) => { answer = resolve; }))
      .mockImplementation((body: SettingsWire) => Promise.resolve(body));
    const { appState, view } = setup(updateSettings);

    choose(view.node(), 'Theme', 'dark');
    await vi.advanceTimersByTimeAsync(700);
    expect(updateSettings).toHaveBeenCalledTimes(1);
    const firstBody = settingsWire(appState.settings!);

    // Somebody carries on while the first save is still on its way.
    choose(view.node(), 'Theme', 'light');
    await vi.advanceTimersByTimeAsync(700);
    answer(firstBody);
    await vi.advanceTimersByTimeAsync(0);

    // The older answer does not put back what was just replaced...
    expect(appState.settings?.general_theme).toBe('light');
    // ...and the newer value is written once the first write is done.
    await vi.waitFor(() => expect(updateSettings).toHaveBeenCalledTimes(2));
    expect(updateSettings.mock.calls[1]?.[0].preferences.general_theme).toBe('light');
  });

  it('saves at once on Close instead of leaving it to a timer', async () => {
    const { view, updateSettings, close } = setup();
    choose(view.node(), 'Theme', 'dark');

    const closeButton = [...view.node().querySelectorAll('button')].find((button) => button.textContent?.includes('Close')) as HTMLButtonElement;
    closeButton.click();

    expect(updateSettings).toHaveBeenCalledTimes(1);
    expect(close).toHaveBeenCalled();
  });

  it('asks before putting a page back to how it started', async () => {
    const { appState, view, updateSettings, confirm } = setup();
    appState.settings!.general_show_thinking = true;
    confirm.mockResolvedValueOnce(false);

    (view.node().querySelector('[data-testid="settings-reset"]') as HTMLButtonElement).click();
    await vi.advanceTimersByTimeAsync(700);
    expect(appState.settings?.general_show_thinking).toBe(true);
    expect(updateSettings).not.toHaveBeenCalled();

    (view.node().querySelector('[data-testid="settings-reset"]') as HTMLButtonElement).click();
    await vi.advanceTimersByTimeAsync(700);
    expect(appState.settings?.general_show_thinking).toBe(false);
    expect(updateSettings).toHaveBeenCalledTimes(1);
  });
});

describe('settings is a list, then a page', () => {
  it('opens at the list when no page is named, and goes back to it', () => {
    expect(parseRoute('#/settings')).toEqual({ kind: 'settings' });
    expect(parseRoute('#/settings/Memory')).toEqual({ kind: 'settings', section: 'Memory' });

    const { appState, view } = setup();
    appState.settingsAtIndex = true;
    // A phone shows the list alone at the index and the page alone after it;
    // the class is what the stylesheet reads.
    expect(view.node().classList.contains('at-index')).toBe(true);

    (view.node().querySelector('[data-testid="settings-nav-memory"]') as HTMLButtonElement).click();
    expect(appState.settingsAtIndex).toBe(false);
    expect(appState.settingsSection).toBe('Memory');
    expect(view.node().classList.contains('at-index')).toBe(false);

    (view.node().querySelector('[data-testid="settings-back"]') as HTMLButtonElement).click();
    expect(appState.settingsAtIndex).toBe(true);
  });
});
