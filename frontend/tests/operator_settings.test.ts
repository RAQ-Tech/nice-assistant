import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { modelSettings, normalizeSettings } from '../src/settings';
import { SettingsView, type Dialogs } from '../src/settings_view';
import { createState } from '../src/state';

const dialogs = {
  prompt: vi.fn(),
  confirm: vi.fn(),
  info: vi.fn(),
} as unknown as Dialogs;

function configuredState() {
  const appState = createState();
  appState.settings = normalizeSettings({
    global_default_model: 'primary-model',
    default_memory_mode: 'saved',
    stt_provider: 'disabled',
    tts_provider: 'disabled',
    tts_format: 'wav',
    openai_api_key: null,
    onboarding_done: true,
    preferences: {},
  });
  appState.models = ['primary-model', 'larger-model'];
  return appState;
}

describe('conversation models', () => {
  it('lists the models Ollama reports, marks the default, and keeps sampling folded', () => {
    const appState = configuredState();
    appState.settingsSection = 'Models';
    const node = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, {} as ApiClient).node();

    const chips = [...node.querySelectorAll('.thing-open')];
    expect(chips.map((chip) => chip.textContent)).toEqual(['primary-modeldefault', 'larger-model']);
    expect(node.querySelectorAll('.info-tip-trigger')).toHaveLength(0);
    expect(node.querySelectorAll('.page-hint')).toHaveLength(1);
    expect((node.querySelector('[data-testid="models-advanced-settings"]') as HTMLDetailsElement).open).toBe(false);
  });

  it('opens a model page that says whose numbers it is using, and customizes on the first change', () => {
    const appState = configuredState();
    appState.settingsSection = 'Models';
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, {} as ApiClient);
    (view.node().querySelector('[data-testid="model-open-primary-model"]') as HTMLButtonElement).click();
    expect(appState.settingsItem).toBe('primary-model');

    const page = view.node();
    expect(page.querySelector('[data-testid="model-settings-provenance"]')?.textContent).toContain('Using the shared defaults');
    const contextRow = [...page.querySelectorAll('.setting-row')]
      .find((row) => row.textContent?.includes('Context window (tokens)')) as HTMLElement;
    const contextInput = contextRow.querySelector('input') as HTMLInputElement;
    contextInput.value = '4096';
    contextInput.dispatchEvent(new Event('input'));
    contextInput.dispatchEvent(new Event('change'));

    expect(appState.settings!.model_overrides['primary-model']?.context_window_tokens).toBe(4096);
    expect(modelSettings(appState.settings!, 'primary-model').context_window_tokens).toBe(4096);
    const customized = view.node();
    expect(customized.querySelector('[data-testid="model-settings-provenance"]')?.textContent).toContain('Customized');
    (customized.querySelector('[data-testid="model-settings-reset"]') as HTMLButtonElement).click();
    expect(appState.settings!.model_overrides['primary-model']).toBeUndefined();
  });

  it('makes a model the default from its own page', () => {
    const appState = configuredState();
    appState.settingsSection = 'Models';
    appState.settingsItem = 'larger-model';
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, {} as ApiClient);
    const toggle = view.node().querySelector('[data-testid="model-settings-default"]') as HTMLInputElement;
    expect(toggle.checked).toBe(false);
    toggle.checked = true;
    toggle.dispatchEvent(new Event('change'));
    expect(appState.settings!.global_default_model).toBe('larger-model');
  });

  it('does not show a misleading global save button on independently persisted operator tabs', () => {
    const appState = configuredState();
    appState.settingsSection = 'Task Models';
    const node = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, {} as ApiClient).node();

    expect(node.querySelector('[data-testid="settings-save"]')).toBeNull();
  });
});
