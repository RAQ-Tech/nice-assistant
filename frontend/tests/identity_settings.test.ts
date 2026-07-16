import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { IdentitySettingsView } from '../src/identity_settings_view';
import { createState } from '../src/state';
import type { Persona, VisualIdentityProfile } from '../src/types';

function persona(): Persona {
  return {
    id: 'nova',
    workspace_id: 'home',
    workspace_ids: ['home'],
    name: 'Nova',
    avatar_url: null,
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

function profile(enabled = true): VisualIdentityProfile {
  return {
    id: enabled ? 'identity-nova' : null,
    persona_id: 'nova',
    status: enabled ? 'draft' : 'disabled',
    consent_status: enabled ? 'granted' : 'not_granted',
    appearance_description: 'Long pink hair and green eyes.',
    acceptance_threshold: 0.78,
    max_generation_attempts: 2,
    failure_policy: 'block_claim',
    revision: enabled ? 1 : 0,
    consent_granted_at: enabled ? 1 : null,
    consent_withdrawn_at: null,
    created_at: enabled ? 1 : null,
    updated_at: enabled ? 1 : null,
    approved_reference_count: 0,
    generation_workflow_configured: false,
    verification_configured: false,
    validation_ready: false,
    references: [],
  };
}

function setup(enabled = true) {
  const appState = createState();
  appState.personas = [persona()];
  appState.identitySelectedPersonaId = 'nova';
  appState.identitySettings = { provider: 'disabled', base_url: '', api_key: '', timeout_seconds: 15 };
  appState.identityProfiles.nova = profile(enabled);
  appState.identityValidations.nova = [];
  appState.identityEvents.nova = [];
  const currentProfile = profile(true);
  const client = {
    mediaLibrary: vi.fn().mockResolvedValue({
      items: [{
        id: 'media-1',
        chat_id: 'chat-1',
        kind: 'image',
        filename: 'generated.png',
        content_url: '/api/v1/media/media-1',
        created_at: 100,
      }],
    }),
    identityReferenceFromMedia: vi.fn().mockResolvedValue({ id: 'reference-1' }),
    visualIdentity: vi.fn().mockResolvedValue(currentProfile),
    identityValidations: vi.fn().mockResolvedValue({ items: [] }),
    identityHistory: vi.fn().mockResolvedValue({ items: [] }),
    grantIdentityConsent: vi.fn().mockResolvedValue(currentProfile),
    mediaUrl: vi.fn((id: string) => `/api/v1/media/${id}`),
  } as unknown as ApiClient;
  const dialogs = {
    prompt: vi.fn(),
    confirm: vi.fn().mockResolvedValue(true),
  };
  const root = document.createElement('div');
  let view!: IdentitySettingsView;
  const render = () => root.replaceChildren(...view.nodes());
  view = new IdentitySettingsView(render, appState, client, dialogs);
  render();
  return { appState, client, dialogs, root };
}

describe('Visual identity settings', () => {
  it('explains readiness and keeps provider plumbing in a closed advanced section', () => {
    const { root } = setup();
    expect(root.textContent).toContain('Keep each persona visually recognizable');
    expect(root.textContent).toContain('ComfyUI needs an identity model plus a bound workflow in Media Catalog');
    expect(root.textContent).toContain('IPAdapter, InstantID, PuLID, or PhotoMaker');
    const blocking = [...root.querySelectorAll('.settings-readiness-row')]
      .find((row) => row.textContent?.includes('Automatic blocking'));
    expect(blocking?.textContent).toContain('Off');
    expect(root.textContent).not.toContain('Protected media ID');
    expect(root.querySelectorAll('.info-tip-trigger').length).toBeGreaterThan(4);
    expect((root.querySelector('[data-testid="identity-advanced-settings"]') as HTMLDetailsElement).open).toBe(false);
  });

  it('selects a generated image through thumbnails instead of requiring a database ID', async () => {
    const { client, root } = setup();
    const attestation = root.querySelector('.identity-attestation input') as HTMLInputElement;
    attestation.checked = true;
    attestation.dispatchEvent(new Event('change', { bubbles: true }));
    (root.querySelector('[data-testid="identity-reference-gallery-open"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.mediaLibrary).toHaveBeenCalledWith('image', 100));
    const picker = root.querySelector('[data-testid="identity-media-picker-reference"]') as HTMLElement;
    expect(picker).not.toBeNull();
    expect(picker.querySelector('img')?.getAttribute('src')).toBe('/api/v1/media/media-1');
    const use = [...picker.querySelectorAll('button')].find((button) => button.textContent === 'Use as reference');
    expect(use?.disabled).toBe(false);
    use?.click();
    await vi.waitFor(() => expect(client.identityReferenceFromMedia).toHaveBeenCalledWith('nova', 'media-1'));
  });

  it('describes fictional-persona rights plainly when visual identity is enabled', async () => {
    const { client, dialogs, root } = setup(false);
    (root.querySelector('[data-testid="identity-enable"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(dialogs.confirm).toHaveBeenCalled());
    expect(dialogs.confirm.mock.calls[0]?.[1]).toContain('does not claim the persona is a real person giving consent');
    await vi.waitFor(() => expect(client.grantIdentityConsent).toHaveBeenCalledWith('nova'));
  });
});
