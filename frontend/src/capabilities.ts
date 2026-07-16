import { api, type ApiClient } from './api';
import { el, errorMessage, markdown } from './dom';
import { extractImageUrl, extractVideoUrl } from './media';
import { machine, state, type ClientStateMachine } from './state';
import type { AppState, CapabilityRequest, IdentitySetupIntent } from './types';

export class CapabilityController {
  private readonly replanningRequestIds = new Set<string>();

  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState = state,
    private readonly stateMachine: ClientStateMachine = machine,
    private readonly client: ApiClient = api,
    private readonly openIdentitySetup: (intent: IdentitySetupIntent) => void = () => undefined,
  ) {}

  node(request: CapabilityRequest): HTMLElement {
    const kind = request.capability_key.endsWith('generate_video') ? 'video' : 'image';
    const prompt = typeof request.arguments.prompt === 'string' ? request.arguments.prompt : '';
    const resultText = typeof request.result?.text === 'string' ? request.result.text : '';
    const imageUrl = extractImageUrl(resultText);
    const videoUrl = extractVideoUrl(resultText);
    const plan = request.media_plan;
    const identityResult = request.result?.identityConditioning;
    const identityWorkflow = request.result?.identityWorkflow;
    const planBlocked = plan?.status === 'blocked';
    const identityBlocked = Boolean(
      planBlocked
      && plan.identity_conditioning?.status !== 'unconditioned'
      && plan.requirements.required_features.includes('identity_control'),
    );
    const identityProfileBlocked = Boolean(identityBlocked && isVisualIdentityBlock(plan?.block?.code));
    const unconditioned = plan?.identity_conditioning?.status === 'unconditioned';
    const approvalBlocked = this.appState.phase !== 'idle';
    const body = resultText ? el('div', { class: 'capability-result', html: markdown(resultText) }) : null;
    body?.querySelectorAll<HTMLImageElement>('img').forEach((image) => {
      image.addEventListener('click', () => {
        this.appState.chatImagePreview = image.src;
        this.renderApp();
      });
    });
    return el('section', { class: `capability-card capability-${request.status}`, 'data-testid': 'capability-request' }, [
      el('div', { class: 'capability-heading' }, [
        el('strong', { textContent: `Requested ${kind}` }),
        el('span', { class: 'capability-status', textContent: statusLabel(request.status) }),
      ]),
      prompt ? el('p', { class: 'capability-prompt', textContent: prompt }) : null,
      plan
        ? el('div', { class: `capability-plan plan-${plan.status}` }, [
            el('strong', {
              textContent: plan.source === 'manual'
                ? 'Manual provider settings'
                : `Media plan: ${plan.status === 'ready' ? 'ready' : 'blocked'}`,
            }),
            plan.selected_resources.length
              ? el('div', {
                  class: 'meta',
                  textContent: `${plan.selected_resources.map((item) => item.name).join(' + ')} · ${plan.estimated_vram_mb || 'unknown'} MB estimated VRAM`,
                })
              : null,
            el('div', { class: 'meta', textContent: plan.block?.message || plan.explanation.summary }),
            plan.identity_conditioning
              ? el('div', {
                  class: 'meta',
                  textContent: identityConditioningMessage(plan.identity_conditioning.status),
                })
              : null,
            ...plan.explanation.warnings.map((warning) => el('div', { class: 'meta', textContent: warning })),
            plan.status === 'blocked' && plan.requirements.required_features.length
              ? el('div', {
                  class: 'meta capability-block-detail',
                  textContent: `Required features: ${plan.requirements.required_features.join(', ')}`,
                })
              : null,
            ...(plan.status === 'blocked'
              ? plan.explanation.rejected.flatMap((candidate) => candidate.reasons.map((reason) => el('div', {
                  class: 'meta capability-block-detail',
                  textContent: `${candidate.name}: ${reason}`,
                })))
              : []),
          ])
        : null,
      request.error ? el('p', { class: 'capability-error', textContent: request.error.message }) : null,
      identityResult
        ? el('p', {
            class: 'meta',
            textContent: identityResult.status === 'unconditioned'
              ? 'No persona identity reference was applied. Resemblance is not guaranteed; this result is explicitly unconditioned and unverified.'
              : identityResult.claim_status === 'verified'
                ? `Persona identity verified${identityWorkflow?.attempts ? ` after ${identityWorkflow.attempts} attempt${identityWorkflow.attempts === 1 ? '' : 's'}` : ''}.`
                : identityResult.verification_status === 'failed'
                  ? 'Persona identity comparison did not pass; this result is explicitly unverified.'
                  : 'Persona reference conditioning was applied, but the verifier was unavailable or inconclusive.',
          })
        : null,
      body,
      videoUrl
        ? el('button', {
            class: 'pill-btn',
            textContent: 'Play video',
            onclick: () => {
              this.appState.chatVideoPreview = videoUrl;
              this.renderApp();
            },
          })
        : null,
      request.status === 'pending_confirmation'
        ? el('div', { class: 'capability-actions' }, [
            planBlocked
              ? el('button', {
                  class: 'send-btn',
                  textContent: identityProfileBlocked
                    ? 'Review visual identity'
                    : identityBlocked
                      ? 'Set up identity control'
                      : 'Try plan again',
                  'data-testid': 'configure-capability',
                  disabled: this.replanningRequestIds.has(request.id),
                  onclick: identityBlocked
                    ? () => this.openIdentitySetup(this.identitySetupIntent(request))
                    : () => void this.replan(request),
                })
              : el('button', {
                  class: 'send-btn',
                  textContent: approvalBlocked
                    ? approvalWaitLabel(this.appState.phase)
                    : unconditioned
                      ? `Generate ${kind} without identity matching`
                      : `Generate ${kind}`,
                  disabled: approvalBlocked,
                  title: approvalBlocked ? approvalWaitMessage(this.appState.phase) : undefined,
                  'data-testid': 'approve-capability',
                  onclick: () => void this.approve(request),
                }),
            identityBlocked
              ? el('button', {
                  class: 'pill-btn',
                  textContent: this.replanningRequestIds.has(request.id) ? 'Checking…' : 'Try plan again',
                  disabled: this.replanningRequestIds.has(request.id),
                  'data-testid': 'retry-capability-plan',
                  onclick: () => void this.replan(request),
                })
              : null,
            el('button', {
              class: 'pill-btn',
              textContent: 'No thanks',
              'data-testid': 'deny-capability',
              onclick: () => void this.deny(request),
            }),
          ])
        : null,
      ['queued', 'running'].includes(request.status)
        ? el('button', {
            class: 'pill-btn',
            textContent: 'Cancel',
            'data-testid': 'cancel-capability',
            onclick: () => void this.cancel(request),
          })
        : null,
      imageUrl ? el('span', { class: 'sr-only', textContent: 'Generated image is ready.' }) : null,
    ]);
  }

  private async approve(request: CapabilityRequest): Promise<void> {
    if (this.appState.phase !== 'idle') return;
    this.stateMachine.transition('queued', 'Capability queued');
    this.renderApp();
    try {
      const approved = await this.client.approveCapability(request.id);
      this.upsert(approved);
      if (!approved.job_id) throw new Error('The capability was approved without a job.');
      this.appState.pendingRequest = {
        jobId: approved.job_id,
        progress: 'Generating…',
        cancel: async () => {
          await this.client.cancelCapability(request.id);
        },
      };
      this.stateMachine.transition('thinking', 'Generating');
      while (true) {
        const current = await this.client.capabilityRequest(request.id);
        this.upsert(current);
        if (!['queued', 'running'].includes(current.status)) break;
        await new Promise((resolve) => window.setTimeout(resolve, 350));
      }
      const final = await this.client.capabilityRequest(request.id);
      this.upsert(final);
      if (final.status === 'failed') this.appState.uiError = final.error?.message || 'Capability failed.';
      this.stateMachine.transition('idle');
    } catch (error) {
      this.appState.uiError = errorMessage(error, 'Unable to run the requested capability.');
      this.stateMachine.transition('error');
    } finally {
      this.appState.pendingRequest = null;
      this.renderApp();
    }
  }

  private async replan(request: CapabilityRequest): Promise<void> {
    if (this.replanningRequestIds.has(request.id)) return;
    this.replanningRequestIds.add(request.id);
    this.renderApp();
    try {
      const replacement = await this.client.replanCapability(request.id);
      this.appState.capabilityRequests = [
        ...this.appState.capabilityRequests.filter((item) => item.id !== request.id && item.id !== replacement.id),
        replacement,
      ].sort((left, right) => left.requested_at - right.requested_at);
    } catch (error) {
      this.appState.uiError = errorMessage(error, 'Unable to check the image plan again.');
    } finally {
      this.replanningRequestIds.delete(request.id);
      this.renderApp();
    }
  }

  private identitySetupIntent(request: CapabilityRequest): IdentitySetupIntent {
    return {
      capability_request_id: request.id,
      chat_id: request.chat_id,
      persona_id: request.media_plan?.identity_conditioning?.persona_id
        ?? this.appState.currentChat?.persona_id
        ?? null,
      prompt: typeof request.arguments.prompt === 'string' ? request.arguments.prompt : '',
      required_features: [...(request.media_plan?.requirements.required_features ?? [])],
      block_code: request.media_plan?.block?.code ?? null,
    };
  }

  private async deny(request: CapabilityRequest): Promise<void> {
    try {
      this.upsert(await this.client.denyCapability(request.id));
    } catch (error) {
      this.appState.uiError = errorMessage(error, 'Unable to deny the capability request.');
    }
    this.renderApp();
  }

  private async cancel(request: CapabilityRequest): Promise<void> {
    try {
      this.upsert(await this.client.cancelCapability(request.id));
      this.appState.pendingRequest = null;
      if (['queued', 'thinking'].includes(this.appState.phase)) this.stateMachine.transition('idle');
    } catch (error) {
      this.appState.uiError = errorMessage(error, 'Unable to cancel the capability request.');
    }
    this.renderApp();
  }

  private upsert(request: CapabilityRequest): void {
    this.appState.capabilityRequests = [
      ...this.appState.capabilityRequests.filter((item) => item.id !== request.id),
      request,
    ].sort((left, right) => left.requested_at - right.requested_at);
  }
}

function statusLabel(status: CapabilityRequest['status']): string {
  return {
    pending_confirmation: 'Approval needed',
    queued: 'Queued',
    running: 'Generating',
    completed: 'Completed',
    failed: 'Failed',
    cancelled: 'Cancelled',
    denied: 'Declined',
    expired: 'Expired',
  }[status];
}

function identityConditioningMessage(status: NonNullable<NonNullable<CapabilityRequest['media_plan']>['identity_conditioning']>['status']): string {
  if (status === 'ready' || status === 'conditioned') {
    return 'Uses the reviewed persona reference for conditioning; the result remains unverified until comparison.';
  }
  if (status === 'unconditioned') {
    return 'No identity workflow will be applied. The image may not resemble the persona and will be labeled unconditioned.';
  }
  return 'Persona identity conditioning is not ready for this request.';
}

function isVisualIdentityBlock(code: string | null | undefined): boolean {
  return [
    'identity_persona_required',
    'identity_profile_unavailable',
    'identity_reference_unavailable',
    'identity_reference_changed',
  ].includes(code ?? '');
}

function approvalWaitLabel(phase: AppState['phase']): string {
  if (phase === 'speaking') return 'Wait for audio to finish';
  if (phase === 'error') return 'Resolve the current error first';
  return 'Wait for the current action';
}

function approvalWaitMessage(phase: AppState['phase']): string {
  if (phase === 'speaking') return 'Stop or finish the current audio before starting image generation.';
  if (phase === 'error') return 'Dismiss the current error before starting image generation.';
  return 'Finish or cancel the current action before starting image generation.';
}
