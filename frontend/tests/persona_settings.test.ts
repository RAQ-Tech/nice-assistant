import { describe, expect, it, vi } from 'vitest';

import type { ApiClient, PersonaInput } from '../src/api';
import { normalizeSettings } from '../src/settings';
import { SettingsView, type Dialogs } from '../src/settings_view';
import { createState } from '../src/state';
import type { Persona } from '../src/types';

function persona(id = 'guide', name = 'Guide'): Persona {
  return {
    id,
    workspace_id: 'home',
    workspace_ids: ['home'],
    name,
    avatar_url: id === 'guide' ? '/api/v1/media/avatar-guide' : null,
    allow_image_sends: true,
    system_prompt: '',
    personality_details: '',
    traits: {},
    default_model: null, voice_preferences: {},
    created_at: 1,
  };
}

function configuredState() {
  const appState = createState();
  appState.settings = normalizeSettings({
    global_default_model: null,
    default_memory_mode: 'saved',
    stt_provider: 'disabled',
    tts_provider: 'disabled',
    tts_format: 'wav',
    openai_api_key: null,
    onboarding_done: true,
    preferences: {},
  });
  appState.settingsSection = 'Personas';
  appState.personas = [persona(), persona('scout', 'Scout')];
  appState.workspaces = [{ id: 'home', name: 'Home', created_at: 1 }];
  return appState;
}

const dialogs = () => ({ prompt: vi.fn(), confirm: vi.fn(), info: vi.fn(), choice: vi.fn() }) as unknown as Dialogs;

describe('the persona list', () => {
  it('is the people, each opening a page of their own', () => {
    const appState = configuredState();
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs(), appState, {} as ApiClient);

    const node = view.node();
    expect(node.querySelectorAll('.thing-open')).toHaveLength(2);
    expect(node.querySelectorAll('.info-tip-trigger')).toHaveLength(0);
    expect(node.querySelector('[data-testid="settings-save"]')).not.toBeNull();
    (node.querySelector('[data-testid="persona-open-scout"]') as HTMLButtonElement).click();

    expect(appState.settingsItem).toBe('scout');
    const page = view.node();
    expect((page.querySelector('[data-testid="persona-page-name"]') as HTMLInputElement).value).toBe('Scout');
    // The page saves itself, so the header's Save has nothing to write here.
    expect(page.querySelector('[data-testid="settings-save"]')).toBeNull();
  });
});

describe('a persona page', () => {
  it('opens the persona avatar in the shared in-app preview state', () => {
    const appState = configuredState();
    appState.settingsItem = 'guide';
    const render = vi.fn();
    const view = new SettingsView(render, vi.fn(), dialogs(), appState, {} as ApiClient);

    const node = view.node();
    const avatar = node.querySelector("[aria-label=\"View Guide's full-size avatar\"]") as HTMLButtonElement;
    avatar.click();

    expect(appState.personaAvatarPreview).toBe('/api/v1/media/avatar-guide');
    expect(render).toHaveBeenCalled();
  });

  it('persists the per-persona image permission independently of direct image actions', async () => {
    const appState = configuredState();
    appState.settingsItem = 'guide';
    const updatePersona = vi.fn().mockImplementation((id: string, input: PersonaInput) =>
      Promise.resolve({ ...persona(), ...input, id }),
    );
    const view = new SettingsView(vi.fn(), vi.fn(), dialogs(), appState, { updatePersona } as unknown as ApiClient);
    const node = view.node();
    expect((node.querySelector('[data-testid="persona-save"]') as HTMLButtonElement).disabled).toBe(true);
    const permissionRow = [...node.querySelectorAll('.setting-toggle-row')]
      .find((row) => row.textContent?.includes('Allowed to send pictures')) as HTMLElement;
    const toggle = permissionRow.querySelector('input') as HTMLInputElement;
    toggle.checked = false;
    toggle.dispatchEvent(new Event('change'));

    const save = view.node().querySelector('[data-testid="persona-save"]') as HTMLButtonElement;
    expect(save.disabled).toBe(false);
    save.click();

    await vi.waitFor(() => expect(updatePersona).toHaveBeenCalled());
    expect(updatePersona.mock.calls[0]?.[1]).toMatchObject({ allow_image_sends: false });
    expect((view.node().querySelector('[data-testid="persona-save"]') as HTMLButtonElement).textContent).toBe('Saved');
  });

  it('asks before leaving with unsaved changes, and puts the persona back when told to', async () => {
    const appState = configuredState();
    appState.settingsItem = 'guide';
    const choice = vi.fn().mockResolvedValue(1);
    const view = new SettingsView(vi.fn(), vi.fn(), { ...dialogs(), choice } as unknown as Dialogs, appState, {} as ApiClient);

    const name = view.node().querySelector('[data-testid="persona-page-name"]') as HTMLInputElement;
    name.value = 'Guide, renamed';
    name.dispatchEvent(new Event('input'));
    expect(appState.personas[0]?.name).toBe('Guide, renamed');

    (view.node().querySelector('[data-testid="persona-page-next"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(appState.settingsItem).toBe('scout'));

    expect(choice).toHaveBeenCalledWith('Save these changes?', expect.stringContaining('unsaved'), expect.any(Array));
    expect(appState.personas[0]?.name).toBe('Guide');
  });

  it('guards the section navigation the same way', async () => {
    const appState = configuredState();
    appState.settingsItem = 'guide';
    const choice = vi.fn().mockResolvedValue(0);
    const view = new SettingsView(vi.fn(), vi.fn(), { ...dialogs(), choice } as unknown as Dialogs, appState, {} as ApiClient);

    const name = view.node().querySelector('[data-testid="persona-page-name"]') as HTMLInputElement;
    name.value = 'Someone else';
    name.dispatchEvent(new Event('input'));
    (view.node().querySelector('[data-testid="settings-nav-general"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(choice).toHaveBeenCalled());

    // Stay here: nothing moved, nothing was lost.
    expect(appState.settingsSection).toBe('Personas');
    expect(appState.settingsItem).toBe('guide');
    expect(appState.personas[0]?.name).toBe('Someone else');
  });
});
