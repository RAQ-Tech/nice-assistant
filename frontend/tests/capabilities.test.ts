import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { CapabilityController } from '../src/capabilities';
import { ClientStateMachine, createState } from '../src/state';
import type { CapabilityRequest } from '../src/types';

function capability(status: CapabilityRequest['status'] = 'pending_confirmation'): CapabilityRequest {
  return {
    id: 'capability-1',
    capability_key: 'media.generate_image',
    status,
    permission_mode: 'confirm',
    arguments: { prompt: 'a moonlit garden' },
    result: null,
    error: null,
    chat_id: 'chat-1',
    turn_id: 'turn-1',
    assistant_message_id: 'message-1',
    job_id: status === 'pending_confirmation' ? null : 'job-1',
    requested_at: 1,
    decided_at: null,
    started_at: null,
    completed_at: null,
    expires_at: null,
    media_plan: null,
  };
}

describe('CapabilityController', () => {
  it('renders approval explicitly and follows the durable request to completion', async () => {
    const appState = createState();
    appState.session = { user_id: 'user-1', expires_at: null, ttl_seconds: 1, is_admin: false };
    appState.phase = 'idle';
    appState.capabilityRequests = [capability()];
    const completed = {
      ...capability('completed'),
      result: {
        text: 'Ready.\n\n![Generated image](/api/v1/media/media-1)',
        mediaId: 'media-1',
      },
    };
    const client = {
      approveCapability: vi.fn().mockResolvedValue(capability('queued')),
      capabilityRequest: vi.fn().mockResolvedValue(completed),
      cancelCapability: vi.fn(),
      denyCapability: vi.fn(),
    } as unknown as ApiClient;
    const render = vi.fn();
    const controller = new CapabilityController(render, appState, new ClientStateMachine(appState), client);
    const node = controller.node(appState.capabilityRequests[0]!);

    expect(node.textContent).toContain('Approval needed');
    (node.querySelector('[data-testid="approve-capability"]') as HTMLButtonElement).click();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(appState.capabilityRequests[0]!.status).toBe('completed');
    expect(client.approveCapability).toHaveBeenCalledWith('capability-1');
    expect(appState.phase).toBe('idle');
  });

  it('records a denial without starting a job', async () => {
    const appState = createState();
    appState.phase = 'idle';
    appState.capabilityRequests = [capability()];
    const client = {
      denyCapability: vi.fn().mockResolvedValue(capability('denied')),
    } as unknown as ApiClient;
    const controller = new CapabilityController(
      () => undefined,
      appState,
      new ClientStateMachine(appState),
      client,
    );
    const node = controller.node(appState.capabilityRequests[0]!);

    (node.querySelector('[data-testid="deny-capability"]') as HTMLButtonElement).click();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(appState.capabilityRequests[0]!.status).toBe('denied');
    expect(client.denyCapability).toHaveBeenCalledWith('capability-1');
  });

  it('disables approval with truthful copy while voice playback is active', () => {
    const appState = createState();
    appState.phase = 'speaking';
    const client = { approveCapability: vi.fn() } as unknown as ApiClient;
    const node = new CapabilityController(
      () => undefined,
      appState,
      new ClientStateMachine(appState),
      client,
    ).node(capability());

    const approve = node.querySelector('[data-testid="approve-capability"]') as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    expect(approve.textContent).toBe('Wait for audio to finish');
    expect(approve.title).toContain('Stop or finish the current audio');
    approve.click();
    expect(client.approveCapability).not.toHaveBeenCalled();
  });

  it('shows blocked details and opens targeted identity setup instead of rendering a dead primary action', () => {
    const appState = createState();
    appState.phase = 'idle';
    const request = capability();
    request.media_plan = {
      id: 'plan-1',
      source: 'coordinator',
      status: 'blocked',
      kind: 'image',
      operation: 'inpaint',
      requirements: {
        kind: 'image', operation: 'inpaint', domains: [], content_tags: [], required_features: ['identity_control'],
      },
      selected_resources: [],
      explanation: {
        summary: 'No executable workflow.',
        selected: [],
        warnings: [],
        rejected: [{ resource_id: 'model-1', name: 'Portrait model', reasons: ['missing required features: identity_control'] }],
      },
      estimated_vram_mb: 0,
      identity_conditioning: null,
      block: { code: 'no_compatible_media_plan', message: 'The adapter cannot execute inpainting.' },
      created_at: 1,
    };
    const client = { approveCapability: vi.fn() } as unknown as ApiClient;
    const openMediaCatalog = vi.fn();
    const node = new CapabilityController(
      () => undefined,
      appState,
      new ClientStateMachine(appState),
      client,
      openMediaCatalog,
    ).node(request);

    expect(node.textContent).toContain('Media plan: blocked');
    expect(node.textContent).toContain('cannot execute inpainting');
    expect(node.textContent).toContain('Required features: identity_control');
    expect(node.textContent).toContain('Portrait model: missing required features: identity_control');
    expect(node.querySelector('[data-testid="approve-capability"]')).toBeNull();
    expect((node.querySelector('[data-testid="configure-capability"]') as HTMLButtonElement).disabled).toBe(false);
    (node.querySelector('[data-testid="configure-capability"]') as HTMLButtonElement).click();
    expect(openMediaCatalog).toHaveBeenCalledWith({
      capability_request_id: 'capability-1',
      chat_id: 'chat-1',
      persona_id: null,
      prompt: 'a moonlit garden',
      required_features: ['identity_control'],
      block_code: 'no_compatible_media_plan',
    });
    expect(client.approveCapability).not.toHaveBeenCalled();
  });

  it('labels profile and reference blocks as visual identity remediation', () => {
    const appState = createState();
    appState.phase = 'idle';
    const request = capability();
    request.media_plan = {
      id: 'plan-identity-reference',
      source: 'coordinator',
      status: 'blocked',
      kind: 'image',
      operation: 'generate',
      requirements: { kind: 'image', operation: 'generate', domains: [], content_tags: [], required_features: ['identity_control'] },
      selected_resources: [],
      explanation: { summary: 'Reference unavailable.', selected: [], warnings: [], rejected: [] },
      estimated_vram_mb: 0,
      identity_conditioning: null,
      block: { code: 'identity_reference_unavailable', message: 'The selected persona needs an approved identity reference.' },
      created_at: 1,
    };
    const openSetup = vi.fn();
    const node = new CapabilityController(
      () => undefined,
      appState,
      new ClientStateMachine(appState),
      {} as ApiClient,
      openSetup,
    ).node(request);

    const configure = node.querySelector('[data-testid="configure-capability"]') as HTMLButtonElement;
    expect(configure.textContent).toBe('Review visual identity');
    configure.click();
    expect(openSetup).toHaveBeenCalledWith(expect.objectContaining({
      capability_request_id: 'capability-1',
      block_code: 'identity_reference_unavailable',
    }));
  });

  it('replaces an immutable blocked request when the user retries its plan', async () => {
    const appState = createState();
    appState.phase = 'idle';
    const request = capability();
    request.media_plan = {
      id: 'plan-blocked',
      source: 'coordinator',
      status: 'blocked',
      kind: 'image',
      operation: 'generate',
      requirements: { kind: 'image', operation: 'generate', domains: [], content_tags: [], required_features: ['identity_control'] },
      selected_resources: [],
      explanation: { summary: 'Identity workflow missing.', selected: [], warnings: [], rejected: [] },
      estimated_vram_mb: 0,
      identity_conditioning: null,
      block: { code: 'no_compatible_media_plan', message: 'Identity workflow missing.' },
      created_at: 1,
    };
    appState.capabilityRequests = [request];
    const replacement = {
      ...request,
      id: 'capability-2',
      requested_at: 2,
      media_plan: { ...request.media_plan, id: 'plan-ready', status: 'ready' as const, block: null },
    };
    const client = { replanCapability: vi.fn().mockResolvedValue(replacement) } as unknown as ApiClient;
    const render = vi.fn();
    const node = new CapabilityController(
      render,
      appState,
      new ClientStateMachine(appState),
      client,
    ).node(request);

    (node.querySelector('[data-testid="retry-capability-plan"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.replanCapability).toHaveBeenCalledWith('capability-1'));
    expect(appState.capabilityRequests.map((item) => item.id)).toEqual(['capability-2']);
    expect(appState.capabilityRequests[0]?.media_plan?.status).toBe('ready');
  });

  it('labels an allowed unconditioned persona plan before approval', () => {
    const appState = createState();
    appState.phase = 'idle';
    const request = capability();
    request.media_plan = {
      id: 'plan-unconditioned',
      source: 'coordinator',
      status: 'ready',
      kind: 'image',
      operation: 'generate',
      requirements: { kind: 'image', operation: 'generate', domains: [], content_tags: [], required_features: ['identity_control'] },
      selected_resources: [],
      explanation: { summary: 'Using explicit unconditioned fallback.', selected: [], warnings: [], rejected: [] },
      estimated_vram_mb: 7000,
      identity_conditioning: {
        required: false,
        status: 'unconditioned',
        mode: null,
        persona_id: 'persona-1',
        profile_id: 'identity-1',
        profile_revision: 2,
        reference_id: null,
        reference_sha256: null,
        workflow_resource_id: null,
        appearance_description_included: false,
        verification_status: 'not_evaluated',
        claim_status: 'unverified',
      },
      block: null,
      created_at: 1,
    };
    const node = new CapabilityController(
      () => undefined,
      appState,
      new ClientStateMachine(appState),
      {} as ApiClient,
    ).node(request);

    expect(node.textContent).toContain('No identity workflow will be applied');
    expect(node.textContent).toContain('Generate image without identity matching');
  });

  it('does not route an unrelated resource failure to identity setup after unconditioned fallback', () => {
    const appState = createState();
    appState.phase = 'idle';
    const request = capability();
    request.media_plan = {
      id: 'plan-resource-blocked',
      source: 'coordinator',
      status: 'blocked',
      kind: 'image',
      operation: 'generate',
      requirements: { kind: 'image', operation: 'generate', domains: [], content_tags: [], required_features: ['identity_control'] },
      selected_resources: [],
      explanation: { summary: 'The selected model cannot start.', selected: [], warnings: [], rejected: [] },
      estimated_vram_mb: 9000,
      identity_conditioning: {
        required: false,
        status: 'unconditioned',
        mode: null,
        persona_id: 'persona-1',
        profile_id: null,
        profile_revision: null,
        reference_id: null,
        reference_sha256: null,
        workflow_resource_id: null,
        appearance_description_included: false,
        verification_status: 'not_evaluated',
        claim_status: 'unverified',
      },
      block: { code: 'insufficient_vram', message: 'Not enough available VRAM.' },
      created_at: 1,
    };
    const openSetup = vi.fn();
    const node = new CapabilityController(
      () => undefined,
      appState,
      new ClientStateMachine(appState),
      {} as ApiClient,
      openSetup,
    ).node(request);

    const configure = node.querySelector('[data-testid="configure-capability"]') as HTMLButtonElement;
    expect(configure.textContent).toBe('Try plan again');
    configure.click();
    expect(openSetup).not.toHaveBeenCalled();
  });

  it('labels reference conditioning separately from identity verification', () => {
    const appState = createState();
    appState.phase = 'idle';
    const request = capability();
    request.media_plan = {
      id: 'plan-identity',
      source: 'coordinator',
      status: 'ready',
      kind: 'image',
      operation: 'generate',
      requirements: {
        kind: 'image', operation: 'generate', domains: [], content_tags: [], required_features: ['identity_control'],
      },
      selected_resources: [],
      explanation: { summary: 'Identity workflow selected.', selected: [], warnings: [], rejected: [] },
      estimated_vram_mb: 7000,
      identity_conditioning: {
        required: true,
        status: 'ready',
        mode: 'approved_reference_workflow',
        persona_id: 'persona-1',
        profile_id: 'identity-1',
        profile_revision: 3,
        reference_id: 'reference-1',
        reference_sha256: 'abc',
        workflow_resource_id: 'workflow-1',
        appearance_description_included: true,
        verification_status: 'not_evaluated',
      },
      block: null,
      created_at: 1,
    };
    const node = new CapabilityController(
      () => undefined,
      appState,
      new ClientStateMachine(appState),
      {} as ApiClient,
    ).node(request);

    expect(node.textContent).toContain('reviewed persona reference');
    expect(node.textContent).toContain('remains unverified');
  });

  it('reports measured identity retries after a verified result', () => {
    const appState = createState();
    const request = capability();
    request.status = 'completed';
    request.result = {
      text: 'Here is your generated image.',
      identityConditioning: { verification_status: 'passed', claim_status: 'verified' },
      identityWorkflow: { attempts: 2, validation: { score: 0.93 } },
    };
    const node = new CapabilityController(
      () => undefined,
      appState,
      new ClientStateMachine(appState),
      {} as ApiClient,
    ).node(request);

    expect(node.textContent).toContain('Persona identity verified after 2 attempts.');
  });

  it('does not claim reference conditioning for a completed unconditioned result', () => {
    const appState = createState();
    const request = capability('completed');
    request.result = {
      text: 'Here is your generated image.',
      identityConditioning: {
        status: 'unconditioned',
        verification_status: 'not_evaluated',
        claim_status: 'unverified',
      },
    };
    const node = new CapabilityController(
      () => undefined,
      appState,
      new ClientStateMachine(appState),
      {} as ApiClient,
    ).node(request);

    expect(node.textContent).toContain('No persona identity reference was applied');
    expect(node.textContent).toContain('Resemblance is not guaranteed');
    expect(node.textContent).toContain('unconditioned and unverified');
    expect(node.textContent).not.toContain('reference conditioning was applied');
  });
});
