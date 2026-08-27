import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { inputField, textareaField } from './settings_controls';
import { settingsCard, settingsHeading } from './settings_ui';
import type { AppState, IdentityWorkflowInspection, MediaCatalogResource } from './types';

interface PendingImport {
  name: string;
  patch: Record<string, unknown>;
  inspection: IdentityWorkflowInspection;
}

/**
 * Bringing your own ComfyUI workflow in, with the format named out loud.
 *
 * There was no import control at all - a workflow was created empty through a
 * bare dialog and then hand-edited as JSON in a collapsed inventory editor,
 * and nothing anywhere said what shape of JSON would be accepted.
 *
 * So: paste the graph, and the page says what it accepts - a workflow exported
 * from ComfyUI in API format. ComfyUI inspects the graph before anything is
 * saved. The request prompt's landing spot comes from that inspection; when
 * the graph offers several text inputs, the person chooses, because the first
 * one found can just as easily be the negative prompt or a filename field.
 * The checkpoint input is bound too when the graph has one, so a preset's own
 * model is what runs rather than whatever name was baked into the export.
 */
export class WorkflowImportView {
  private name = '';
  private kind: 'image' | 'video' = 'image';
  private raw = '';
  private message = '';
  private busy = false;
  private pending: PendingImport | null = null;
  private promptChoice = '';

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
    private readonly refreshCatalog: () => Promise<void>,
  ) {}

  node(models: MediaCatalogResource[]): HTMLElement {
    const comfyModels = models.filter((model) => model.backend === 'comfyui' && model.kind === this.kind);
    return settingsCard([
      settingsHeading(
        'Bring your own workflow',
        'Paste a workflow exported from ComfyUI in API format: in ComfyUI, open the Workflow menu and choose '
          + 'Export (API). Ordinary saves, screenshots, and spreadsheets cannot be imported.',
      ),
      inputField('Workflow name', this.name, (value) => { this.name = value; }, 'text', false,
        'Your label for this workflow in the catalog.'),
      el('div', { class: 'setting-row' }, [
        el('label', { textContent: 'This workflow makes' }),
        el('select', {
          class: 'chip-select',
          'data-testid': 'workflow-import-kind',
          onchange: (event: Event) => {
            this.kind = (event.currentTarget as HTMLSelectElement).value === 'video' ? 'video' : 'image';
            this.renderApp();
          },
        }, [
          el('option', { value: 'image', selected: this.kind === 'image', textContent: 'Pictures' }),
          el('option', { value: 'video', selected: this.kind === 'video', textContent: 'Video clips' }),
        ]),
      ]),
      textareaField('Workflow JSON (API format)', this.raw, (value) => { this.raw = value; }, false,
        'The pasted graph is checked against ComfyUI before it is saved; nothing runs during the check.'),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: this.busy ? 'Checking with ComfyUI…' : 'Check workflow',
          disabled: this.busy || !comfyModels.length,
          'data-testid': 'workflow-import-submit',
          onclick: () => void this.check(),
        }),
      ]),
      ...this.landingSpot(comfyModels),
      !comfyModels.length
        ? el('p', {
            class: 'meta',
            textContent: this.kind === 'video'
              ? 'Add a ComfyUI video model first — in Operator tools, add a model and set its kind to video.'
              : 'Add a ComfyUI model first — a workflow needs a model to run on.',
          })
        : null,
      this.message
        ? el('p', { class: 'meta', 'data-testid': 'workflow-import-message', textContent: this.message })
        : null,
    ]);
  }

  /**
   * Where the request prompt lands, chosen by a person when it is ambiguous.
   *
   * ComfyUI reports every literal text input as a possible landing spot - and
   * in a real export that list includes the negative prompt and the save
   * node's filename. Guessing the first one would wire what somebody asks for
   * into the wrong box and nothing would ever say so.
   */
  private landingSpot(comfyModels: MediaCatalogResource[]): HTMLElement[] {
    const pending = this.pending;
    if (!pending) return [];
    const candidates = pending.inspection.request_input_candidates?.prompt ?? [];
    return [
      el('div', { class: 'setting-row' }, [
        el('label', { textContent: 'Where should what the chat asks for land?' }),
        el(
          'select',
          {
            class: 'chip-select',
            'data-testid': 'workflow-import-prompt-choice',
            onchange: (event: Event) => {
              this.promptChoice = (event.currentTarget as HTMLSelectElement).value;
              this.renderApp();
            },
          },
          candidates.map((candidate) => {
            const key = `${candidate.node_id}:${candidate.input_name}`;
            const preview = String(candidate.current_value ?? '').slice(0, 60);
            return el('option', {
              value: key,
              selected: key === this.promptChoice,
              textContent: preview ? `${candidate.label} — now "${preview}"` : candidate.label,
            });
          }),
        ),
        el('p', {
          class: 'meta',
          textContent: 'The saved text shown beside each input is the best clue: pick the one holding the positive prompt.',
        }),
      ]),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: this.busy ? 'Adding…' : `Add "${pending.name}"`,
          disabled: this.busy,
          'data-testid': 'workflow-import-confirm',
          onclick: () => void this.create(comfyModels),
        }),
        el('button', {
          class: 'pill-btn',
          textContent: 'Cancel',
          disabled: this.busy,
          onclick: () => {
            this.pending = null;
            this.message = '';
            this.renderApp();
          },
        }),
      ]),
    ];
  }

  private async check(): Promise<void> {
    const name = this.name.trim();
    if (!name) {
      this.message = 'Give the workflow a name first.';
      this.renderApp();
      return;
    }
    let patch: Record<string, unknown>;
    try {
      patch = JSON.parse(this.raw);
      if (!patch || typeof patch !== 'object' || Array.isArray(patch)) throw new Error('not an object');
    } catch {
      this.message = 'That is not JSON. In ComfyUI use Workflow → Export (API), then paste the whole file.';
      this.renderApp();
      return;
    }
    this.busy = true;
    this.pending = null;
    this.renderApp();
    try {
      const inspection = await this.client.inspectIdentityWorkflow(patch, {}, 'general');
      const candidates = inspection.request_input_candidates?.prompt ?? [];
      if (!inspection.provider_compatible || !candidates.length) {
        this.message = inspection.message || 'ComfyUI could not accept this workflow.';
        this.renderApp();
        return;
      }
      const first = candidates[0]!;
      this.pending = { name, patch, inspection };
      this.promptChoice = `${first.node_id}:${first.input_name}`;
      this.message = candidates.length > 1
        ? `ComfyUI accepted the graph and found ${candidates.length} text inputs. Choose where the prompt lands.`
        : 'ComfyUI accepted the graph. Confirm to add it.';
    } catch (error) {
      this.message = errorMessage(error, 'The workflow could not be checked.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  private async create(comfyModels: MediaCatalogResource[]): Promise<void> {
    const pending = this.pending;
    if (!pending) return;
    const [nodeId, inputName] = this.promptChoice.split(':');
    if (!nodeId || !inputName) return;
    const seed = pending.inspection.request_input_candidates?.seed?.[0];
    const checkpoint = pending.inspection.request_input_candidates?.checkpoint?.[0];
    this.busy = true;
    this.renderApp();
    try {
      await this.client.createMediaCatalogResource({
        resource_type: 'workflow',
        kind: this.kind,
        name: pending.name,
        provider_key: this.kind === 'video' ? 'local-video' : 'local-image',
        backend: 'comfyui',
        external_id: `imported-${Date.now().toString(36)}`,
        enabled: true,
        priority: 50,
        operations: ['generate'],
        domains: [],
        content_tags: [],
        features: [],
        estimated_vram_mb: 0,
        estimated_load_seconds: 0,
        default_settings: {
          workflow_patch: pending.patch,
          prompt_bindings: [{ node_id: nodeId, input_name: inputName }],
          ...(seed ? { seed_bindings: [{ node_id: seed.node_id, input_name: seed.input_name }] } : {}),
          // The checkpoint binding is what lets a preset's own model run
          // instead of whatever filename was baked into the export - and it is
          // the only honest basis for pairing this graph with several models.
          ...(checkpoint
            ? { checkpoint_bindings: [{ node_id: checkpoint.node_id, input_name: checkpoint.input_name }] }
            : {}),
        },
        notes: 'Imported by pasting a ComfyUI API-format export.',
        // Without a checkpoint binding the graph always loads its baked-in
        // file, so only the model matching that file can honestly pair with it.
        compatible_model_ids: checkpoint
          ? comfyModels.map((model) => model.id)
          : comfyModels
              .filter((model) => model.external_id === String(checkpointName(pending.patch) ?? ''))
              .map((model) => model.id),
      });
      const where = pending.inspection.request_input_candidates?.prompt?.find(
        (candidate) => `${candidate.node_id}:${candidate.input_name}` === this.promptChoice,
      );
      this.message = `"${pending.name}" was added. The prompt lands on ${where?.label ?? 'the chosen input'}`
        + (checkpoint ? ', and presets will run their own model through it.' : '.');
      this.name = '';
      this.raw = '';
      this.pending = null;
      await this.refreshCatalog();
    } catch (error) {
      this.message = errorMessage(error, 'The workflow could not be added.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }
}

/** The checkpoint filename baked into a pasted graph, if one is legible. */
function checkpointName(patch: Record<string, unknown>): string | null {
  for (const node of Object.values(patch)) {
    if (!node || typeof node !== 'object') continue;
    const inputs = (node as { inputs?: Record<string, unknown> }).inputs;
    // Image graphs bake ckpt_name; video graphs load their model as a UNET.
    for (const key of ['ckpt_name', 'unet_name', 'checkpoint_name']) {
      const name = inputs?.[key];
      if (typeof name === 'string' && name) return name;
    }
  }
  return null;
}
