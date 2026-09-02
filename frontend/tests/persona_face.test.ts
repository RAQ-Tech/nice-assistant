import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { PersonaFaceView } from '../src/persona_face_view';
import { createState } from '../src/state';
import type { IdentityReference, Persona, VisualIdentityProfile } from '../src/types';

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
    voice_preferences: {},
    created_at: 1,
  };
}

function profile(overrides: Partial<VisualIdentityProfile> = {}): VisualIdentityProfile {
  return {
    id: 'identity-nova',
    persona_id: 'nova',
    status: 'draft',
    consent_status: 'granted',
    appearance_description: 'Long pink hair and green eyes.',
    acceptance_threshold: 0.78,
    max_generation_attempts: 2,
    conditioning_mechanism: 'reference_adapter',
    comparison_retry_enabled: false,
    preferred_preset_ids: [],
    failure_policy: 'show_unverified',
    conditioning_fallback: 'allow_unconditioned',
    revision: 1,
    consent_granted_at: 1,
    consent_withdrawn_at: null,
    created_at: 1,
    updated_at: 1,
    approved_reference_count: 0,
    generation_workflow_configured: false,
    verification_configured: false,
    validation_ready: false,
    references: [],
    ...overrides,
  };
}

function reference(overrides: Partial<IdentityReference> = {}): IdentityReference {
  return {
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
    ...overrides,
  };
}

function setup(current: VisualIdentityProfile | null = profile()) {
  const appState = createState();
  appState.personas = [persona()];
  if (current) appState.identityProfiles.nova = current;
  const client = {
    visualIdentity: vi.fn().mockResolvedValue(profile({ references: [reference()], approved_reference_count: 1 })),
    grantIdentityConsent: vi.fn().mockResolvedValue(profile()),
    withdrawIdentityConsent: vi.fn().mockResolvedValue(profile({ id: null, consent_status: 'withdrawn', status: 'disabled' })),
    updateVisualIdentity: vi.fn().mockImplementation((_id: string, updated: VisualIdentityProfile) => Promise.resolve({ ...updated })),
    identityReferenceFromMedia: vi.fn().mockResolvedValue({ id: 'reference-2' }),
    approveIdentityReference: vi.fn().mockResolvedValue({}),
    rejectIdentityReference: vi.fn().mockResolvedValue({}),
    deleteIdentityReference: vi.fn().mockResolvedValue({ ok: true }),
    mediaLibrary: vi.fn().mockResolvedValue({
      items: [{ id: 'media-1', chat_id: 'chat-1', kind: 'image', filename: 'generated.png', content_url: '/api/v1/media/media-1', created_at: 100 }],
    }),
  } as unknown as ApiClient;
  const dialogs = { prompt: vi.fn().mockResolvedValue('Not them.'), confirm: vi.fn().mockResolvedValue(true) };
  const openSetup = vi.fn();
  const openMore = vi.fn();
  const root = document.createElement('div');
  let view!: PersonaFaceView;
  const render = () => root.replaceChildren(view.node(persona()));
  view = new PersonaFaceView(appState, client, render, dialogs, openSetup, openMore);
  render();
  return { appState, client, dialogs, openSetup, openMore, root, render, view };
}

function flip(root: HTMLElement, on: boolean): void {
  const toggle = root.querySelector('[data-testid="persona-face-switch"]') as HTMLInputElement;
  toggle.checked = on;
  toggle.dispatchEvent(new Event('change'));
}

describe('a persona’s face', () => {
  it('is off until the switch is turned on, and turning it on states the rights plainly', async () => {
    const { client, dialogs, root } = setup(profile({ id: null, consent_status: 'not_granted', status: 'disabled', revision: 0 }));
    expect((root.querySelector('[data-testid="persona-face-switch"]') as HTMLInputElement).checked).toBe(false);
    expect(root.querySelector('[data-testid="persona-face-photos"]')).toBeNull();

    flip(root, true);
    await vi.waitFor(() => expect(dialogs.confirm).toHaveBeenCalled());
    expect(dialogs.confirm.mock.calls[0]?.[1]).toContain('does not claim the persona is a real person giving consent');
    await vi.waitFor(() => expect(client.grantIdentityConsent).toHaveBeenCalledWith('nova'));
    await vi.waitFor(() => expect(root.querySelector('[data-testid="persona-face-photos"]')).not.toBeNull());
  });

  it('asks for a photo, and takes a generated picture in one motion once the rights are ticked', async () => {
    const { client, root } = setup();
    expect(root.querySelector('[data-testid="persona-face-hint"]')?.textContent).toContain('Add a photo');
    expect((root.querySelector('[data-testid="identity-reference-file"]') as HTMLInputElement).disabled).toBe(true);

    const attestation = root.querySelector('.identity-attestation input') as HTMLInputElement;
    attestation.checked = true;
    attestation.dispatchEvent(new Event('change'));
    expect((root.querySelector('[data-testid="identity-reference-file"]') as HTMLInputElement).disabled).toBe(false);

    (root.querySelector('[data-testid="identity-reference-gallery-open"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.mediaLibrary).toHaveBeenCalledWith('image', 100));
    const picker = root.querySelector('[data-testid="identity-media-picker-reference"]') as HTMLElement;
    const use = [...picker.querySelectorAll('button')].find((button) => button.textContent === 'Use as reference') as HTMLButtonElement;
    expect(use.disabled).toBe(false);
    use.click();
    await vi.waitFor(() => expect(client.identityReferenceFromMedia).toHaveBeenCalledWith('nova', 'media-1'));
    // The reply is re-read rather than guessed, and the photo appears.
    await vi.waitFor(() => expect(root.querySelector('[data-testid="identity-reference-reference-1"]')).not.toBeNull());
    expect(root.querySelector('[data-testid="persona-face-hint"]')).toBeNull();
  });

  it('asks for a hand only when the photo has nothing that can use it', () => {
    const waiting = setup(profile({ references: [reference()], approved_reference_count: 1 }));
    expect(waiting.root.querySelector('[data-testid="persona-face-hint"]')).toBeNull();
    const needs = waiting.root.querySelector('[data-testid="persona-face-needs"]') as HTMLElement;
    expect(needs.textContent).toContain('no identity workflow is installed');
    (needs.querySelector('[data-testid="identity-configure-generation"]') as HTMLButtonElement).click();
    expect(waiting.openSetup).toHaveBeenCalledWith('nova');

    const ready = setup(profile({ references: [reference()], approved_reference_count: 1, generation_workflow_configured: true }));
    expect(ready.root.querySelector('[data-testid="persona-face-needs"]')).toBeNull();
    expect(ready.root.querySelector('[data-testid="persona-face-hint"]')).toBeNull();
    expect(ready.root.querySelectorAll('.info-tip-trigger')).toHaveLength(0);
  });

  it('lets a generated picture in only after a yes', async () => {
    const { client, root } = setup(profile({
      references: [reference({ id: 'reference-9', review_status: 'pending', is_primary: false, provenance: 'generated_approved' })],
    }));
    const pending = root.querySelector('[data-testid="identity-reference-reference-9"]') as HTMLElement;
    expect(pending.textContent).toContain('Is this Nova?');
    ([...pending.querySelectorAll('button')].find((button) => button.textContent === 'Yes') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.approveIdentityReference).toHaveBeenCalledWith('reference-9'));
  });

  it('saves what the fold changes, and offers a technique only when there is a choice', async () => {
    const { appState, client, root, render } = setup();
    expect(root.querySelector('[data-testid="identity-mechanism"]')?.tagName).not.toBe('SELECT');
    const fallback = root.querySelector('[data-testid="identity-fallback"]') as HTMLSelectElement;
    fallback.value = 'require_conditioning';
    fallback.dispatchEvent(new Event('change'));
    await vi.waitFor(() => expect(client.updateVisualIdentity).toHaveBeenCalledWith('nova', expect.objectContaining({
      conditioning_fallback: 'require_conditioning',
    })));

    appState.identityProfiles.nova!.available_mechanisms = ['identity_pass', 'reference_adapter'];
    render();
    const mechanism = root.querySelector('[data-testid="identity-mechanism"]') as HTMLSelectElement;
    expect(mechanism.tagName).toBe('SELECT');
    expect([...mechanism.options].map((option) => option.value)).toEqual(['identity_pass', 'reference_adapter']);
  });

  it('says the photos are deleted before turning the face off', async () => {
    const { client, dialogs, root } = setup(profile({ references: [reference()], approved_reference_count: 1 }));
    flip(root, false);
    await vi.waitFor(() => expect(dialogs.confirm).toHaveBeenCalled());
    expect(dialogs.confirm.mock.calls[0]?.[1]).toContain('deleted');
    await vi.waitFor(() => expect(client.withdrawIdentityConsent).toHaveBeenCalledWith('nova'));
    await vi.waitFor(() => expect((root.querySelector('[data-testid="persona-face-switch"]') as HTMLInputElement).checked).toBe(false));
  });

  it('leaves the face alone when the person changes their mind', async () => {
    const { client, dialogs, root } = setup();
    dialogs.confirm.mockResolvedValue(false);
    flip(root, false);
    await vi.waitFor(() => expect(dialogs.confirm).toHaveBeenCalled());
    expect(client.withdrawIdentityConsent).not.toHaveBeenCalled();
    expect((root.querySelector('[data-testid="persona-face-switch"]') as HTMLInputElement).checked).toBe(true);
  });

  it('reaches the rest in one press, and loads a face it has not seen', async () => {
    const { client, openMore, root, view } = setup();
    (root.querySelector('[data-testid="persona-face-more"]') as HTMLButtonElement).click();
    expect(openMore).toHaveBeenCalledWith('nova');

    const fresh = setup(null);
    expect(fresh.root.textContent).toContain('Checking…');
    await fresh.view.load('nova');
    expect(fresh.client.visualIdentity).toHaveBeenCalledWith('nova');
    expect(fresh.root.querySelector('[data-testid="identity-reference-reference-1"]')).not.toBeNull();
    // Loading is once: a second call is not a second request.
    await view.load('nova');
    expect(client.visualIdentity).not.toHaveBeenCalled();
  });
});
