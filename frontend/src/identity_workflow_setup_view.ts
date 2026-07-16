import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { inputField, selectField, textareaField } from './settings_controls';
import type { SettingsDialogs } from './settings_contracts';
import { settingsCard, settingsHeading, titleCase } from './settings_ui';
import type { AppState, IdentityWorkflowInspection, MediaCatalogResource } from './types';

const MAX_WORKFLOW_BYTES = 200_000;

export class IdentityWorkflowSetupView {
  private setupOpen = false;
  private workflowJson = '';
  private workflowPatch: Record<string, unknown> | null = null;
  private inspection: IdentityWorkflowInspection | null = null;
  private binding = '';
  private modelId = '';
  private workflowName = 'Identity control workflow';
  private inspectionResultNode: HTMLElement | null = null;
  private bindingFieldNode: HTMLElement | null = null;
  private liveTestWarningNode: HTMLElement | null = null;
  private saveButton: HTMLButtonElement | null = null;

  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly dialogs: SettingsDialogs,
    private readonly finishSetup: () => void,
    private readonly refreshCatalog: () => Promise<void>,
  ) {}

  open(): void {
    this.setupOpen = true;
  }

  node(): HTMLElement {
    const catalog = this.appState.mediaCatalog;
    if (!catalog) return el('div');
    const workflows = catalog.resources.filter((item) =>
      item.resource_type === 'workflow'
      && item.kind === 'image'
      && item.backend === 'comfyui'
      && item.features.includes('identity_control')
    );
    const configured = workflows.filter((item) =>
      item.enabled
      && item.compatible_model_ids.length > 0
      && Array.isArray(item.default_settings.identity_image_bindings)
      && item.default_settings.identity_image_bindings.length > 0
    );
    const models = catalog.resources.filter((item) =>
      item.enabled
      && item.resource_type === 'model'
      && item.kind === 'image'
      && item.provider_key === 'local-image'
      && item.backend === 'comfyui'
    );
    if (!models.some((item) => item.id === this.modelId)) this.modelId = models[0]?.id ?? '';
    const intent = this.appState.mediaCatalogIdentitySetupIntent;
    const personaName = intent?.persona_id
      ? this.appState.personas.find((item) => item.id === intent.persona_id)?.name ?? 'the selected persona'
      : 'a selected persona';
    const setupOpen = this.setupOpen;

    return settingsCard([
      el('div', { class: 'task-model-head' }, [
        el('div', {}, [
          el('strong', { textContent: 'Identity control' }),
          el('div', {
            class: 'meta',
            textContent: 'Reference-aware generation uses a real ComfyUI API workflow, not a filename or prompt-only identity claim.',
          }),
        ]),
        el('span', {
          class: `provider-status ${configured.length ? 'ok' : 'fail'}`,
          textContent: configured.length ? 'Configured' : 'Not configured',
        }),
      ]),
      intent?.capability_request_id
        ? el('div', {
            class: 'settings-warning',
            textContent: `The blocked image request for ${personaName} requires identity_control. Import and check a compatible workflow here, then retry that exact request.`,
          })
        : intent
          ? el('div', {
              class: 'meta',
              textContent: `Set up a reference-aware ComfyUI workflow for ${personaName}. No blocked image request is attached to this setup.`,
            })
          : null,
      configured.length
        ? el('div', { class: 'meta', textContent: `${configured.length} enabled identity workflow${configured.length === 1 ? '' : 's'} can receive an approved persona reference.` })
        : el('div', {
            class: 'settings-empty-state',
            textContent: 'No enabled identity_control workflow has an explicit reference-image binding and compatible ComfyUI base model.',
          }),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'pill-btn',
          textContent: setupOpen ? 'Hide identity setup' : (configured.length ? 'Review identity setup' : 'Add identity control'),
          'data-testid': 'identity-workflow-setup-toggle',
          onclick: () => {
            this.setupOpen = !setupOpen;
            this.renderApp();
          },
        }),
        intent?.capability_request_id
          ? el('button', {
              class: 'send-btn',
              textContent: this.appState.mediaCatalogBusy ? 'Checking plan…' : 'Retry this image plan',
              disabled: this.appState.mediaCatalogBusy,
              'data-testid': 'identity-workflow-retry-plan',
              onclick: () => void this.retryPlan(),
            })
          : null,
      ]),
      ...(setupOpen ? this.setupFields(models) : []),
    ], 'identity-control-card');
  }

  private setupFields(models: MediaCatalogResource[]): HTMLElement[] {
    const inspection = this.inspection;
    const candidates = inspection?.identity_input_candidates ?? [];
    const selected = candidates.find((item) => bindingKey(item.node_id, item.input_name) === this.binding);
    const canSave = Boolean(inspection?.provider_compatible && this.workflowPatch && selected && this.modelId);
    const workflowField = textareaField(
      'Or paste API workflow JSON',
      this.workflowJson,
      (value) => this.workflowChanged(value),
      false,
      'Use ComfyUI API-format JSON. Browser-format workflow files cannot be executed by the provider API.',
    );
    workflowField.querySelector('textarea')?.setAttribute('data-testid', 'identity-workflow-json');
    this.inspectionResultNode = inspection ? this.inspectionResult(inspection) : el('div', {
      class: 'meta',
      textContent: 'No provider check has run. Saving metadata alone is not proof that ComfyUI can execute the workflow.',
    });
    this.inspectionResultNode.dataset.testid = 'identity-workflow-inspection-result';
    this.bindingFieldNode = inspection && candidates.length
      ? selectField(
          'Persona reference image input',
          this.binding,
          candidates.map((item) => bindingKey(item.node_id, item.input_name)),
          (value) => { this.binding = value; },
          'identity-workflow-binding',
          (value) => candidates.find((item) => bindingKey(item.node_id, item.input_name) === value)?.label || value,
          false,
          'Nice Assistant uploads the approved reference only to this selected node and input. It does not infer custom-node bindings.',
        )
      : null;
    this.liveTestWarningNode = inspection?.provider_compatible && !inspection.live_tested
      ? el('div', {
          class: 'settings-warning',
          textContent: 'Provider-compatible, not generation-tested: no image has been generated yet. The first approved persona request remains the live execution test.',
        })
      : null;
    this.saveButton = el('button', {
      class: 'send-btn',
      textContent: this.appState.mediaCatalogBusy ? 'Saving…' : 'Save and enable identity control',
      disabled: this.appState.mediaCatalogBusy || !canSave,
      'data-testid': 'identity-workflow-save',
      onclick: () => void this.saveWorkflow(),
    }) as HTMLButtonElement;
    return [
      settingsHeading(
        'Guided ComfyUI workflow setup',
        'Export a working workflow from ComfyUI in API format. Nice Assistant checks deployed nodes and model-like inputs, then asks which exact image input receives the approved persona reference.',
      ),
      models.length
        ? selectField(
            'Catalog base model to pair with this workflow',
            this.modelId,
            models.map((item) => item.id),
            (value) => { this.modelId = value; },
            'identity-workflow-model',
            (value) => models.find((item) => item.id === value)?.name ?? value,
            false,
            'This declares planning compatibility with the selected catalog model. Provider inspection does not verify that pairing; the first generation remains its live test.',
          )
        : el('div', {
            class: 'settings-warning',
            textContent: 'Add and enable a ComfyUI image base model before importing an identity workflow.',
          }),
      inputField(
        'Workflow name',
        this.workflowName,
        (value) => { this.workflowName = value; },
        'text',
        false,
        'A recognizable catalog name, such as Persona IPAdapter portrait workflow.',
      ),
      el('label', { class: 'setting-row identity-workflow-file-row' }, [
        el('span', { textContent: 'Import ComfyUI API workflow JSON' }),
        el('input', {
          type: 'file',
          accept: 'application/json,.json',
          'data-testid': 'identity-workflow-file',
          onchange: (event: Event) => void this.loadFile(event),
        }),
      ]),
      workflowField,
      el('button', {
        class: 'pill-btn',
        textContent: this.appState.mediaCatalogBusy ? 'Checking provider…' : 'Check workflow against ComfyUI',
        disabled: this.appState.mediaCatalogBusy || models.length === 0,
        'data-testid': 'identity-workflow-inspect',
        onclick: () => void this.inspectWorkflow(),
      }),
      this.inspectionResultNode,
      this.bindingFieldNode,
      this.liveTestWarningNode,
      this.saveButton,
    ].filter((node): node is HTMLElement => Boolean(node));
  }

  private inspectionResult(inspection: IdentityWorkflowInspection): HTMLElement {
    const unavailableAssets = (inspection.asset_checks ?? []).filter((item) => !item.available);
    return el('div', { class: `media-plan-preview plan-${inspection.provider_compatible ? 'ready' : 'blocked'}` }, [
      el('strong', { textContent: inspection.provider_compatible ? 'Provider-compatible' : titleCase(inspection.status) }),
      el('p', { textContent: inspection.message }),
      inspection.detected_node_types?.length
        ? el('div', { class: 'meta', textContent: `Workflow node types: ${inspection.detected_node_types.join(', ')}` })
        : null,
      inspection.missing_node_types?.length
        ? el('div', { class: 'meta capability-block-detail', textContent: `Missing node types: ${inspection.missing_node_types.join(', ')}` })
        : null,
      ...unavailableAssets.map((item) => el('div', {
        class: 'meta capability-block-detail',
        textContent: `${item.node_type} ${item.input_name}: ${item.value || 'required asset'} is not available`,
      })),
      ...(inspection.warnings ?? []).map((warning) => el('div', { class: 'meta', textContent: warning })),
    ]);
  }

  private async loadFile(event: Event): Promise<void> {
    const file = (event.currentTarget as HTMLInputElement).files?.[0];
    if (!file) return;
    if (file.size > MAX_WORKFLOW_BYTES) {
      this.appState.settingsError = 'The ComfyUI workflow file is too large. Export a focused API workflow no larger than 200 KB.';
      this.renderApp();
      return;
    }
    try {
      this.workflowJson = await file.text();
      this.workflowPatch = null;
      this.inspection = null;
      this.binding = '';
      this.appState.settingsError = '';
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to read the ComfyUI workflow file.');
    }
    this.renderApp();
  }

  private parseWorkflow(): Record<string, unknown> | null {
    try {
      const parsed: unknown = JSON.parse(this.workflowJson || '{}');
      if (!isRecord(parsed)) throw new Error('Workflow JSON must be an object.');
      const workflow = isRecord(parsed.prompt) ? parsed.prompt : parsed;
      if (!Object.keys(workflow).length) throw new Error('Workflow JSON is empty.');
      if (jsonByteSize(workflow) > MAX_WORKFLOW_BYTES) {
        throw new Error('Workflow JSON must be no larger than 200 KB after parsing.');
      }
      this.appState.settingsError = '';
      return workflow;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'The imported ComfyUI API workflow is not valid JSON.');
      return null;
    }
  }

  private async inspectWorkflow(): Promise<void> {
    const workflow = this.parseWorkflow();
    if (!workflow) {
      this.renderApp();
      return;
    }
    this.appState.mediaCatalogBusy = true;
    this.inspection = null;
    this.renderApp();
    try {
      const inspection = await this.client.inspectIdentityWorkflow(workflow);
      this.workflowPatch = workflow;
      this.inspection = inspection;
      const first = inspection.identity_input_candidates[0];
      this.binding = first ? bindingKey(first.node_id, first.input_name) : '';
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to check the workflow against ComfyUI.');
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private async saveWorkflow(): Promise<void> {
    const candidate = this.inspection?.identity_input_candidates.find((item) =>
      bindingKey(item.node_id, item.input_name) === this.binding
    );
    if (!this.inspection?.provider_compatible || !this.workflowPatch || !candidate || !this.modelId) {
      this.appState.settingsError = 'Check a provider-compatible workflow, choose its persona reference input, and select a base model before saving.';
      this.renderApp();
      return;
    }
    this.appState.mediaCatalogBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      await this.client.createMediaCatalogResource({
        resource_type: 'workflow',
        kind: 'image',
        name: this.workflowName.trim() || 'Identity control workflow',
        provider_key: 'local-image',
        backend: 'comfyui',
        external_id: `identity-control-${Date.now().toString(36)}`,
        enabled: true,
        priority: 60,
        operations: ['generate'],
        domains: ['portrait'],
        content_tags: [],
        features: ['identity_control'],
        estimated_vram_mb: 0,
        estimated_load_seconds: 0,
        default_settings: {
          workflow_patch: this.workflowPatch,
          identity_image_bindings: [{ node_id: candidate.node_id, input_name: candidate.input_name }],
        },
        notes: 'Imported through guided identity control setup. Provider compatibility checked; live generation not yet tested.',
        compatible_model_ids: [this.modelId],
      });
      await this.refreshCatalog();
      this.dialogs.info(
        'Identity control added',
        'ComfyUI reported compatible nodes, configured workflow assets, and a reference input. Nice Assistant recorded your selected catalog model as an explicit pairing; that pairing and generation are not live-tested until the next approved persona image runs.',
      );
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to save the identity workflow.');
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private async retryPlan(): Promise<void> {
    const intent = this.appState.mediaCatalogIdentitySetupIntent;
    if (!intent?.capability_request_id) return;
    this.appState.mediaCatalogBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      const replacement = await this.client.replanCapability(intent.capability_request_id);
      this.appState.capabilityRequests = [
        ...this.appState.capabilityRequests.filter((item) =>
          item.id !== intent.capability_request_id && item.id !== replacement.id
        ),
        replacement,
      ].sort((left, right) => left.requested_at - right.requested_at);
      if (replacement.media_plan?.status === 'ready') {
        this.appState.mediaCatalogIdentitySetupIntent = null;
        this.dialogs.info(
          'Image plan ready',
          replacement.media_plan.identity_conditioning?.status === 'unconditioned'
            ? 'The replacement plan is ready without identity matching and will say so before generation.'
            : 'The replacement plan is ready. Review and approve it back in the chat.',
        );
        this.finishSetup();
      } else {
        this.appState.settingsError = replacement.media_plan?.block?.message
          || 'The replacement plan is still blocked. Review the identity control status below.';
      }
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to retry the original image plan.');
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private workflowChanged(value: string): void {
    this.workflowJson = value;
    this.workflowPatch = null;
    this.inspection = null;
    this.binding = '';
    const parsedSize = workflowJsonByteSize(value);
    const message = parsedSize !== null && parsedSize > MAX_WORKFLOW_BYTES
      ? 'This workflow is larger than the 200 KB catalog limit. Reduce it before checking the provider.'
      : 'Workflow changed. Check it against ComfyUI again before saving.';
    if (this.inspectionResultNode) {
      this.inspectionResultNode.className = 'meta';
      this.inspectionResultNode.textContent = message;
    }
    this.bindingFieldNode?.remove();
    this.bindingFieldNode = null;
    this.liveTestWarningNode?.remove();
    this.liveTestWarningNode = null;
    if (this.saveButton) this.saveButton.disabled = true;
  }
}

function bindingKey(nodeId: string, inputName: string): string {
  return `${nodeId}:${inputName}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function workflowJsonByteSize(value: string): number | null {
  try {
    const parsed: unknown = JSON.parse(value || '{}');
    if (!isRecord(parsed)) return null;
    return jsonByteSize(isRecord(parsed.prompt) ? parsed.prompt : parsed);
  } catch {
    return null;
  }
}

function jsonByteSize(value: Record<string, unknown>): number {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}
