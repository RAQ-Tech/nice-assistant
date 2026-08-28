import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { IdentitySettingsView } from '../src/identity_settings_view';
import { createState } from '../src/state';
import type { IdentityReference, IdentityValidation, Persona, VisualIdentityProfile } from '../src/types';

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
    default_model: null, voice_preferences: {},
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
    conditioning_mechanism: 'reference_adapter',
    comparison_retry_enabled: true,
    preferred_preset_ids: ['preset-a'],
    failure_policy: 'block_claim',
    conditioning_fallback: 'allow_unconditioned',
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
    updateVisualIdentity: vi.fn().mockImplementation((_personaId: string, updated: VisualIdentityProfile) => Promise.resolve({ ...updated })),
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
  return { appState, client, dialogs, root, render };
}

describe('Visual identity settings', () => {
  it('explains readiness, exposes behavior controls, and keeps provider plumbing advanced', () => {
    const { root } = setup();
    expect(root.textContent).toContain("Everything about a persona's pictures");
    expect(root.textContent).toContain('ComfyUI needs an identity model plus a bound workflow in Media Catalog');
    expect(root.textContent).toContain('IPAdapter, InstantID, PuLID, or PhotoMaker');
    // Readiness reports readiness; the two policy rows that once restated
    // the dropdowns below are gone, and the policies live only where they
    // are set.
    const readinessText = [...root.querySelectorAll('.settings-readiness-row')].map((row) => row.textContent).join(' ');
    expect(readinessText).not.toContain('When identity control is unavailable');
    expect(readinessText).not.toContain('When comparison fails');
    expect(root.textContent).toContain('Identity generation behavior');
    expect(root.textContent).toContain('Generate and label an unconditioned image');
    expect(root.textContent).not.toContain('Protected media ID');
    expect(root.querySelectorAll('.info-tip-trigger').length).toBeGreaterThan(4);
    expect((root.querySelector('[data-testid="identity-advanced-settings"]') as HTMLDetailsElement).open).toBe(false);
  });

  it('saves conditioning fallback separately from comparison failure behavior', async () => {
    const { client, root } = setup();
    const fallbackRow = [...root.querySelectorAll('.setting-row')]
      .find((row) => row.textContent?.includes('When identity control is unavailable')) as HTMLElement;
    const fallback = fallbackRow.querySelector('select') as HTMLSelectElement;
    fallback.value = 'require_conditioning';
    fallback.dispatchEvent(new Event('change', { bubbles: true }));
    const comparisonRow = [...root.querySelectorAll('.setting-row')]
      .find((row) => row.textContent?.includes('When optional comparison fails')) as HTMLElement;
    const comparison = comparisonRow.querySelector('select') as HTMLSelectElement;
    comparison.value = 'show_unverified';
    comparison.dispatchEvent(new Event('change', { bubbles: true }));
    (root.querySelector('[data-testid="identity-behavior-save"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(client.updateVisualIdentity).toHaveBeenCalled());
    expect(client.updateVisualIdentity).toHaveBeenCalledWith('nova', expect.objectContaining({
      conditioning_fallback: 'require_conditioning',
      failure_policy: 'show_unverified',
    }));
  });

  it('keeps the comparison threshold behind the advanced disclosure', () => {
    const { root } = setup();
    const advanced = root.querySelector('[data-testid="identity-advanced-settings"]') as HTMLElement;
    const threshold = [...root.querySelectorAll('.setting-row')]
      .find((row) => row.textContent?.includes('Comparison threshold')) as HTMLElement;
    // docs/settings-experience.md: thresholds and verifier plumbing are
    // advanced. Comparison is advisory (ADR 0031), so it must not be the
    // second thing somebody meets while setting a persona up.
    expect(advanced.contains(threshold)).toBe(true);
    expect(root.querySelector('[data-testid="identity-comparison-policy-save"]')).not.toBeNull();
  });

  it('names how the face is produced rather than offering a choice of one', () => {
    const { root } = setup();
    expect(root.textContent).toContain('Condition generation on the reference image');
    // This catalog can apply one technique, so there is nothing to choose
    // between and a select would be a control that cannot do anything.
    expect(root.querySelector('[data-testid="identity-mechanism"]')?.tagName).not.toBe('SELECT');
  });

  it('offers a choice once the catalog can apply more than one technique', () => {
    const { appState, root, render } = setup();
    appState.identityProfiles.nova!.available_mechanisms = ['identity_pass', 'reference_adapter'];
    render();

    const control = root.querySelector('[data-testid="identity-mechanism"]') as HTMLSelectElement;
    expect(control.tagName).toBe('SELECT');
    expect([...control.options].map((option) => option.value)).toEqual(['identity_pass', 'reference_adapter']);
    expect(root.textContent).toContain('Replace the face after generation');
  });

  it('turns readiness summaries into direct setup actions', () => {
    const { root } = setup();
    expect((root.querySelector('[data-testid="identity-configure-generation"]') as HTMLButtonElement).disabled).toBe(false);
    (root.querySelector('[data-testid="identity-configure-comparison"]') as HTMLButtonElement).click();
    expect((root.querySelector('[data-testid="identity-advanced-settings"]') as HTMLDetailsElement).open).toBe(true);
    expect(root.textContent).toContain('Optional identity comparison service');
  });

  it('selects a generated image through thumbnails instead of requiring a database ID', async () => {
    const { appState, client, root } = setup();
    const attestation = root.querySelector('.identity-attestation input') as HTMLInputElement;
    attestation.checked = true;
    attestation.dispatchEvent(new Event('change', { bubbles: true }));
    (root.querySelector('[data-testid="identity-reference-gallery-open"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.mediaLibrary).toHaveBeenCalledWith('image', 100));
    const picker = root.querySelector('[data-testid="identity-media-picker-reference"]') as HTMLElement;
    expect(picker).not.toBeNull();
    expect(picker.querySelector('img')?.getAttribute('src')).toBe('/api/v1/media/media-1');
    (picker.querySelector('[aria-label="Open generated image preview"]') as HTMLButtonElement).click();
    expect(appState.chatImagePreview).toBe('/api/v1/media/media-1');
    const refreshedPicker = root.querySelector('[data-testid="identity-media-picker-reference"]') as HTMLElement;
    const use = [...refreshedPicker.querySelectorAll('button')].find((button) => button.textContent === 'Use as reference');
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

  it('opens reference and comparison thumbnails in the shared in-app image viewer', () => {
    const { appState, root, render } = setup();
    appState.identityProfiles.nova!.references = [{
      id: 'reference-1',
      persona_id: 'nova',
      source_media_id: null,
      content_url: '/api/v1/identity-references/reference-1/content',
      content_type: 'image/png',
      byte_size: 1024,
      width: 512,
      height: 512,
      sha256: 'abc123',
      provenance: 'user_upload',
      review_status: 'approved',
      is_primary: true,
      rejection_reason: null,
      created_at: 1,
      reviewed_at: 1,
      deleted_at: null,
    } satisfies IdentityReference];
    appState.identityValidations.nova = [{
      id: 'validation-1',
      persona_id: 'nova',
      candidate_media_id: 'media-1',
      sequence_number: 1,
      created_order: 1,
      job_id: 'job-1',
      matched_reference_id: 'reference-1',
      provider: 'disabled',
      status: 'passed',
      failure_policy: 'show_unverified',
      claim_status: 'verified',
      score: 0.9,
      threshold: 0.78,
      source_face_count: 1,
      target_face_count: 1,
      provider_version: null,
      request_id: null,
      error: null,
      created_at: 1,
      started_at: 1,
      completed_at: 2,
    } satisfies IdentityValidation];
    render();

    (root.querySelector('[aria-label="Open Nova reference image"]') as HTMLButtonElement).click();
    expect(appState.chatImagePreview).toBe('/api/v1/identity-references/reference-1/content');

    (root.querySelector('[aria-label="Open compared image for Nova"]') as HTMLButtonElement).click();
    expect(appState.chatImagePreview).toBe('/api/v1/media/media-1');
  });
});
