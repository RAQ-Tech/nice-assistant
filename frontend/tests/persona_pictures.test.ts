import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { PersonaPicturesView } from '../src/persona_pictures_view';
import { createState } from '../src/state';
import type { Persona, VisualIdentityProfile } from '../src/types';

/**
 * A persona's pictures live on the persona's page now: the preferred recipes
 * and the kept pictures under the face, and what comparison needs inside the
 * face's own fold. Nothing a persona owns is shown on two pages.
 */

function persona(): Persona {
  return {
    id: 'nova', workspace_id: 'home', workspace_ids: ['home'], name: 'Nova', avatar_url: null,
    system_prompt: '', personality_details: '', traits: {}, default_model: null, voice_preferences: {}, created_at: 1,
  };
}

function profile(): VisualIdentityProfile {
  return {
    id: 'identity-nova', persona_id: 'nova', status: 'draft', consent_status: 'granted',
    appearance_description: 'Long pink hair and green eyes.', acceptance_threshold: 0.78, max_generation_attempts: 2,
    conditioning_mechanism: 'reference_adapter', comparison_retry_enabled: true, preferred_preset_ids: ['preset-a'],
    failure_policy: 'block_claim', conditioning_fallback: 'allow_unconditioned', revision: 1, consent_granted_at: 1,
    consent_withdrawn_at: null, created_at: 1, updated_at: 1, approved_reference_count: 0,
    generation_workflow_configured: false, verification_configured: false, validation_ready: false, references: [],
  };
}

function setup() {
  const appState = createState();
  appState.personas = [persona()];
  appState.identitySettings = { provider: 'disabled', base_url: '', api_key: '', timeout_seconds: 15 };
  appState.identityProfiles.nova = profile();
  appState.identityValidations.nova = [];
  appState.identityEvents.nova = [];
  const client = {
    visualIdentity: vi.fn().mockResolvedValue(profile()),
    identityValidations: vi.fn().mockResolvedValue({ items: [] }),
    identityHistory: vi.fn().mockResolvedValue({ items: [] }),
    libraryEntries: vi.fn().mockResolvedValue({ items: [] }),
    mediaPresets: vi.fn().mockResolvedValue({ items: [{ id: 'preset-a', name: 'Portraits' }, { id: 'preset-b', name: 'Landscapes' }] }),
    updateVisualIdentity: vi.fn().mockImplementation((_id: string, updated: VisualIdentityProfile) => Promise.resolve({ ...updated })),
    updateIdentitySettings: vi.fn().mockImplementation((settings: unknown) => Promise.resolve(settings)),
    mediaUrl: vi.fn((id: string) => `/api/v1/media/${id}`),
  } as unknown as ApiClient;
  const view = new PersonaPicturesView(() => undefined, appState, client);
  return { appState, client, view };
}

describe("a persona's pictures on the persona's page", () => {
  it('shows the preferred recipes and the kept pictures under the face, with help on hover', async () => {
    const { view } = setup();
    await view.load('nova');
    const node = view.node(persona());

    expect(node.textContent).toContain('Preferred recipes');
    expect(node.textContent).toContain('Portraits');
    expect(node.textContent).toContain('Kept pictures');
    expect(node.textContent).toContain('Nothing kept yet');
    expect(node.querySelectorAll('.info-tip-trigger')).toHaveLength(0);
    expect(node.querySelector('.settings-subheading')?.getAttribute('title')).toContain('best first');
  });

  it('loads a persona once: the verifier settings, its comparisons, history and kept pictures', async () => {
    const { appState, client, view } = setup();
    appState.identitySettings = null;
    const identitySettings = vi.fn().mockResolvedValue({ provider: 'disabled', base_url: '', api_key: '', timeout_seconds: 15 });
    (client as unknown as { identitySettings: unknown }).identitySettings = identitySettings;

    await view.load('nova');
    await view.load('nova');

    expect(identitySettings).toHaveBeenCalledTimes(1);
    expect(client.visualIdentity).toHaveBeenCalledTimes(1);
    expect(client.identityHistory).toHaveBeenCalledWith('nova');
    expect(client.libraryEntries).toHaveBeenCalledWith('nova');
  });

  it('keeps comparison inside the fold, and saves the outcome separately from the face', async () => {
    const { client, view } = setup();
    const fold = document.createElement('div');
    fold.append(...view.foldContent(persona()));

    expect(fold.textContent).toContain('Optional identity comparison service');
    expect(fold.textContent).toContain('Comparison threshold');
    expect(fold.textContent).toContain('Manual comparison');
    expect(fold.textContent).toContain('Activity history');
    expect(fold.querySelectorAll('.info-tip-trigger')).toHaveLength(0);
    // The recipes and kept pictures are not repeated inside the fold.
    expect(fold.textContent).not.toContain('Preferred recipes');

    const comparisonRow = [...fold.querySelectorAll('.setting-row')]
      .find((row) => row.textContent?.includes('When optional comparison fails')) as HTMLElement;
    const comparison = comparisonRow.querySelector('select') as HTMLSelectElement;
    comparison.value = 'show_unverified';
    comparison.dispatchEvent(new Event('change', { bubbles: true }));
    (fold.querySelector('[data-testid="identity-comparison-policy-save"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(client.updateVisualIdentity).toHaveBeenCalled());
    expect(client.updateVisualIdentity).toHaveBeenCalledWith('nova', expect.objectContaining({
      failure_policy: 'show_unverified',
      conditioning_fallback: 'allow_unconditioned',
    }));
  });

  it('says the comparison service is off in words, and saves it when changed', async () => {
    const { client, view } = setup();
    const fold = document.createElement('div');
    fold.append(...view.foldContent(persona()));
    const service = [...fold.querySelectorAll('select')].find((select) =>
      [...select.options].some((option) => option.textContent === 'CompreFace')) as HTMLSelectElement;
    expect(service.selectedOptions[0]?.textContent).toBe('Off');

    (fold.querySelector('[data-testid="identity-provider-save"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.updateIdentitySettings).toHaveBeenCalledWith(expect.objectContaining({ provider: 'disabled' })));
  });
});
