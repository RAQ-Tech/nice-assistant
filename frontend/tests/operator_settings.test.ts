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

describe('operator settings progressive disclosure', () => {
  it('keeps optional model controls closed while exposing a useful readiness summary', () => {
    const appState = configuredState();
    appState.settingsSection = 'Models';
    const node = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, {} as ApiClient).node();

    expect(node.textContent).toContain('2 reported by Ollama');
    expect(node.textContent).toContain('4096 tokens for primary-model');
    expect((node.querySelector('[data-testid="models-advanced-settings"]') as HTMLDetailsElement).open).toBe(false);
    expect((node.querySelector('[data-testid="model-overrides-settings"]') as HTMLDetailsElement).open).toBe(false);
  });

  it('creates a per-model customization that changes the effective runtime settings', () => {
    const appState = configuredState();
    appState.settingsSection = 'Models';
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, {} as ApiClient);
    const firstNode = view.node();
    const customize = [...firstNode.querySelectorAll('button')]
      .find((button) => button.textContent === 'Customize primary-model') as HTMLButtonElement;
    customize.click();

    const override = view.node().querySelector('[data-testid="model-overrides-settings"]') as HTMLDetailsElement;
    const contextRow = [...override.querySelectorAll('.setting-row')]
      .find((row) => row.textContent?.includes('Context window tokens')) as HTMLElement;
    const contextInput = contextRow.querySelector('input') as HTMLInputElement;
    contextInput.value = '8192';
    contextInput.dispatchEvent(new Event('input'));

    expect(appState.settings!.model_overrides['primary-model']?.context_window_tokens).toBe(8192);
    expect(modelSettings(appState.settings!, 'primary-model').context_window_tokens).toBe(8192);
  });

  it('does not show a misleading global save button on independently persisted operator tabs', () => {
    const appState = configuredState();
    appState.settingsSection = 'Task Models';
    const node = new SettingsView(vi.fn(), vi.fn(), dialogs, appState, {} as ApiClient).node();

    expect(node.querySelector('[data-testid="settings-save"]')).toBeNull();
  });
});
