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

  it('shows the selected resources and prevents approval for a blocked plan', () => {
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
    expect((node.querySelector('[data-testid="approve-capability"]') as HTMLButtonElement).disabled).toBe(true);
    (node.querySelector('[data-testid="configure-capability"]') as HTMLButtonElement).click();
    expect(openMediaCatalog).toHaveBeenCalledOnce();
    expect(client.approveCapability).not.toHaveBeenCalled();
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
});
