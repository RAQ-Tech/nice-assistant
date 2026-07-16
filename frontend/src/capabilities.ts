import { api, type ApiClient } from './api';
import { el, errorMessage, markdown } from './dom';
import { extractImageUrl, extractVideoUrl } from './media';
import { machine, state, type ClientStateMachine } from './state';
import type { AppState, CapabilityRequest } from './types';

export class CapabilityController {
  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState = state,
    private readonly stateMachine: ClientStateMachine = machine,
    private readonly client: ApiClient = api,
    private readonly openMediaCatalog: () => void = () => undefined,
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
                  textContent: plan.identity_conditioning.status === 'ready'
                    ? 'Uses the reviewed persona reference for conditioning; the result remains unverified until comparison.'
                    : 'Persona identity conditioning is not ready for this request.',
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
            textContent: identityResult.claim_status === 'verified'
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
            el('button', {
              class: 'send-btn',
              textContent: plan?.status === 'blocked' ? 'Plan unavailable' : `Generate ${kind}`,
              disabled: plan?.status === 'blocked',
              'data-testid': 'approve-capability',
              onclick: () => void this.approve(request),
            }),
            plan?.status === 'blocked'
              ? el('button', {
                  class: 'pill-btn',
                  textContent: 'Open Media Catalog',
                  'data-testid': 'configure-capability',
                  onclick: this.openMediaCatalog,
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
