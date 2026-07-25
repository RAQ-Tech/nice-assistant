import { describe, expect, it, vi } from 'vitest';

import type { ApiClient, PersonaInput } from '../src/api';
import { normalizeSettings } from '../src/settings';
import { SettingsView, type Dialogs } from '../src/settings_view';
import { createState } from '../src/state';
import type { Chat, Persona } from '../src/types';

function persona(): Persona {
  return {
    id: 'guide',
    workspace_id: 'home',
    workspace_ids: ['home'],
    name: 'Guide',
    avatar_url: '/api/v1/media/avatar-guide',
    allow_image_sends: true,
    system_prompt: '',
    personality_details: '',
    traits: {},
    default_model: null,
    preferred_voice: null,
    preferred_tts_model: null,
    preferred_tts_speed: null,
    preferred_voice_openai: null,
    preferred_tts_model_openai: null,
    preferred_tts_speed_openai: null,
    preferred_voice_local: null,
    preferred_tts_model_local: null,
    preferred_tts_speed_local: null,
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
  appState.personas = [persona()];
  appState.workspaces = [{ id: 'home', name: 'Home', created_at: 1 }];
  return appState;
}

function boundChat(): Chat {
  return {
    id: 'chat-1',
    workspace_id: 'home',
    persona_id: 'guide',
    binding: {
      human_id: 'human-1',
      persona_id: 'guide',
      persona_name: 'Guide',
      binding_status: 'active',
      context: {
        kind: 'workspace',
        workspace_id: 'home',
        workspace_name: 'Home',
      },
      can_continue: true,
      block_code: null,
      block_message: null,
    },
    model_override: null,
    memory_mode: 'saved',
    title: 'Bound chat',
    hidden_in_ui: false,
    created_at: 1,
    updated_at: 1,
  };
}

describe('persona settings', () => {
  it('opens the persona avatar in the shared in-app preview state', () => {
    const appState = configuredState();
    const render = vi.fn();
    const view = new SettingsView(
      render,
      vi.fn(),
      { prompt: vi.fn(), confirm: vi.fn(), info: vi.fn() } as unknown as Dialogs,
      appState,
      {} as ApiClient,
    );

    const node = view.node();
    const avatar = node.querySelector("[aria-label=\"View Guide's full-size avatar\"]") as HTMLButtonElement;
    avatar.click();

    expect(appState.personaAvatarPreview).toBe('/api/v1/media/avatar-guide');
    expect(render).toHaveBeenCalled();
  });

  it('persists the per-persona image permission independently of direct image actions', async () => {
    const appState = configuredState();
    const updatePersona = vi.fn().mockImplementation((id: string, input: PersonaInput) =>
      Promise.resolve({ ...persona(), ...input, id }),
    );
    const view = new SettingsView(
      vi.fn(),
      vi.fn(),
      { prompt: vi.fn(), confirm: vi.fn(), info: vi.fn() } as unknown as Dialogs,
      appState,
      { updatePersona } as unknown as ApiClient,
    );
    const node = view.node();
    const permissionRow = [...node.querySelectorAll('.setting-toggle-row')]
      .find((row) => row.textContent?.includes('Allow persona to send images')) as HTMLElement;
    const toggle = permissionRow.querySelector('input') as HTMLInputElement;
    toggle.checked = false;
    toggle.dispatchEvent(new Event('change'));

    const save = [...node.querySelectorAll('button')]
      .find((button) => button.textContent === 'Save persona') as HTMLButtonElement;
    save.click();

    await vi.waitFor(() => expect(updatePersona).toHaveBeenCalled());
    expect(updatePersona.mock.calls[0]?.[1]).toMatchObject({ allow_image_sends: false });
  });

  it('refreshes the current chat binding after a persona mutation', async () => {
    const appState = configuredState();
    const chat = boundChat();
    const blocked = {
      ...chat,
      binding: {
        ...chat.binding,
        persona_name: 'Guide renamed',
        can_continue: false,
        block_code: 'persona_not_in_workspace',
        block_message: 'This persona is no longer available in this workspace.',
      },
    } satisfies Chat;
    appState.currentChat = chat;
    appState.chats = [chat];
    appState.newChatContextKey = 'workspace:home';
    appState.personas[0]!.name = 'Guide renamed';
    const updatePersona = vi.fn().mockResolvedValue({ ...persona(), name: 'Guide renamed' });
    const chats = vi.fn().mockResolvedValue({ items: [blocked] });
    const view = new SettingsView(
      vi.fn(),
      vi.fn(),
      { prompt: vi.fn(), confirm: vi.fn(), info: vi.fn() } as unknown as Dialogs,
      appState,
      { updatePersona, chats } as unknown as ApiClient,
    );

    const node = view.node();
    const save = [...node.querySelectorAll('button')]
      .find((button) => button.textContent === 'Save persona') as HTMLButtonElement;
    save.click();

    await vi.waitFor(() => expect(chats).toHaveBeenCalledOnce());
    expect(appState.currentChat?.binding.can_continue).toBe(false);
    expect(appState.currentChat?.binding.persona_name).toBe('Guide renamed');
    expect(appState.newChatContextKey).toBe('');
  });
});
