import type { ApiClient } from './api';
import type { SettingsDialogs } from './settings_contracts';
import { el, errorMessage } from './dom';
import { selectControl as select, settingField as field, settingsHeading, titleCase } from './settings_ui';
import type {
  AppState,
  IdentityWorkflowInspection,
  MediaPreset,
  WorkflowTemplate,
  WorkflowTemplateList,
} from './types';

const MECHANISM_LABELS: Record<string, string> = {
  reference_adapter: 'Conditions generation on the reference',
  identity_pass: 'Replaces the face after generation',
};

/**
 * The shipped graphs, offered instead of asking somebody to read a node graph.
 *
 * Checking a template answers one question - can this ComfyUI run it - and says
 * plainly what it could not check. Installing writes a graph whose bindings
 * came with it, so nothing here asks which input receives the prompt.
 */
export class WorkflowTemplateView {
  private list: WorkflowTemplateList | null = null;
  private loadedModelId = '';
  private busy = false;
  private checks: Record<string, IdentityWorkflowInspection> = {};
  private presets: MediaPreset[] = [];
  private presetId = '';
  // Keyed by template, then by `nodeId:inputName`. A downloaded model keeps the
  // name its source gave it, so the graph is pointed at the file rather than
  // the file renamed to match the graph.
  private assetChoices: Record<string, Record<string, string>> = {};

  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly refreshCatalog: () => Promise<void>,
    private readonly dialogs: SettingsDialogs,
  ) {}

  node(modelId: string, modelName: string): HTMLElement {
    if (modelId && modelId !== this.loadedModelId) {
      // Claimed before the request starts, not after it succeeds. Rendering is
      // what triggers the load, so a failure that left this unset would start
      // another load on the very next render, forever.
      this.loadedModelId = modelId;
      void this.load(modelId);
    }
    const templates = this.list?.templates ?? [];
    return el('div', { class: 'workflow-template-list', 'data-testid': 'workflow-templates' }, [
      settingsHeading(
        'Start from a known-good workflow',
        'These graphs ship with Nice Assistant, already wired to receive the request and the persona reference. Checking one asks ComfyUI whether the nodes and files it names are installed. Nothing here has been generation-tested on this deployment.',
      ),
      this.list && !this.list.model_architecture
        ? el('div', {
            class: 'meta',
            'data-testid': 'workflow-template-architecture-unknown',
            textContent: `${modelName} has no declared model family, so every template is offered. Recording the family in the model's settings is what lets a mismatch be caught before it wastes a generation.`,
          })
        : null,
      this.busy && !templates.length
        ? el('div', { class: 'meta', textContent: 'Loading templates…' })
        : templates.length
          ? el('div', {}, templates.map((template) => this.card(template, modelId)))
          : el('div', { class: 'settings-empty-state', textContent: 'No shipped workflow templates are available.' }),
    ]);
  }

  private card(template: WorkflowTemplate, modelId: string): HTMLElement {
    const check = this.checks[template.id];
    return el('div', { class: 'persona-card workflow-template-card', 'data-testid': `workflow-template-${template.id}` }, [
      el('div', { class: 'task-model-head' }, [
        el('div', {}, [
          el('strong', { textContent: template.name }),
          el('div', { class: 'meta', textContent: MECHANISM_LABELS[template.mechanism] ?? template.mechanism }),
        ]),
        el('span', {
          class: `provider-status ${template.installed_resource_id ? 'ok' : 'idle'}`,
          'data-testid': `workflow-template-status-${template.id}`,
          // The count is the tell for the click-it-again trap: five installs
          // of one template are five identical graphs, and nothing on screen
          // used to say so.
          textContent: template.installed_count > 1
            ? `Installed ✓ — ${template.installed_count} copies`
            : template.installed_resource_id
              ? 'Installed ✓'
              : 'Not installed',
        }),
      ]),
      el('p', { class: 'meta', textContent: template.summary }),
      template.architecture_matches
        ? null
        : el('div', {
            class: 'settings-warning',
            'data-testid': `workflow-template-mismatch-${template.id}`,
            textContent: `Built for ${template.architectures.join(', ')} checkpoints, and this model is declared as ${this.list?.model_architecture}. It can still be installed, but likeness on a different family is unmeasured.`,
          }),
      template.update_available
        ? el('div', {
            class: 'meta',
            'data-testid': `workflow-template-update-${template.id}`,
            textContent: `A newer version of this template ships with Nice Assistant. Installing it adds a second workflow rather than rewriting the one you have, which may have been tuned.`,
          })
        : null,
      el('div', { class: 'meta' }, [
        el('strong', { textContent: 'You need to have installed:' }),
        el('ul', {}, template.required_assets.map((item) => el('li', { textContent: item }))),
      ]),
      check ? this.checkResult(check) : null,
      ...this.assetPickers(template, check),
      // A later pass is useless on its own: something has to run it after a
      // picture exists. Choosing the recipe here is what keeps that out of a
      // hand-edited definition JSON.
      template.mechanism === 'identity_pass' && this.presets.length
        ? field(
            'Add it to this recipe as a second pass',
            this.presetSelect(),
            'The recipe generates the picture, and this pass replaces the face in it afterwards.',
          )
        : null,
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'pill-btn',
          textContent: this.busy ? 'Checking…' : 'Check against ComfyUI',
          disabled: this.busy,
          'data-testid': `workflow-template-verify-${template.id}`,
          onclick: () => void this.verify(template.id),
        }),
        el('button', {
          class: template.installed_resource_id ? 'pill-btn' : 'send-btn',
          textContent: this.busy
            ? 'Adding…'
            : template.installed_resource_id
              ? 'Install another copy'
              : 'Add this workflow',
          disabled: this.busy || !modelId,
          'data-testid': `workflow-template-install-${template.id}`,
          onclick: () => void this.install(template, modelId),
        }),
      ]),
    ]);
  }

  private assetPickers(template: WorkflowTemplate, check?: IdentityWorkflowInspection): HTMLElement[] {
    const missing = (check?.asset_checks ?? []).filter((item) => !item.available && (item.options?.length ?? 0) > 0);
    return missing.map((item) => {
      const key = `${item.node_id}:${item.input_name}`;
      const chosen = this.assetChoices[template.id]?.[key] ?? '';
      const control = select(chosen, ['', ...(item.options ?? [])], (value) => {
        this.assetChoices[template.id] = { ...(this.assetChoices[template.id] ?? {}), [key]: value };
      }, (value) => value || `Keep ${item.value}`);
      control.dataset.testid = `workflow-template-asset-${template.id}-${item.input_name}`;
      return field(
        `${item.node_type} needs a file for ${item.input_name}`,
        control,
        `The template names ${item.value}, which ComfyUI does not report. These are the files it does have for this input. Choosing one points the graph at your file instead of asking you to rename it.`,
      );
    });
  }

  private presetSelect(): HTMLSelectElement {
    const control = select(
      this.presetId,
      ['', ...this.presets.map((item) => item.id)],
      (value) => { this.presetId = value; },
      (value) => this.presets.find((item) => item.id === value)?.name ?? 'Do not add it to a recipe yet',
    );
    control.dataset.testid = 'workflow-template-preset';
    return control;
  }

  private checkResult(check: IdentityWorkflowInspection): HTMLElement {
    const unavailable = (check.asset_checks ?? []).filter((item) => !item.available);
    return el('div', { class: `media-plan-preview plan-${check.provider_compatible ? 'ready' : 'blocked'}` }, [
      el('strong', { textContent: check.provider_compatible ? 'ComfyUI can run this' : titleCase(check.status) }),
      el('p', { textContent: check.message }),
      check.missing_node_types?.length
        ? el('div', {
            class: 'meta capability-block-detail',
            textContent: `Not installed: ${check.missing_node_types.join(', ')}`,
          })
        : null,
      ...unavailable.map((item) => el('div', {
        class: 'meta capability-block-detail',
        textContent: `${item.node_type} ${item.input_name}: ${item.value || 'required file'} is not available`,
      })),
      el('div', {
        class: 'meta',
        // The honest limit of this check, stated where the result is read.
        textContent: 'This checks node types and the files nodes name. A model a node picks by device rather than by name cannot be checked from here, and no image has been generated.',
      }),
    ]);
  }

  private async load(modelId: string): Promise<void> {
    this.busy = true;
    try {
      const [list, presets] = await Promise.all([
        this.client.workflowTemplates(modelId),
        this.client.mediaPresets(),
      ]);
      this.list = list;
      this.presets = presets.items.filter((item) => item.kind === 'image');
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Workflow templates could not be loaded.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  private async verify(templateId: string): Promise<void> {
    this.busy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      this.checks[templateId] = await this.client.verifyWorkflowTemplate(templateId);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'ComfyUI could not be asked about this template.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }

  private async install(template: WorkflowTemplate, modelId: string): Promise<void> {
    // Reinstalling is legitimate - a copy can be tuned separately - but it has
    // to be chosen, not stumbled into. Five identical InstantID graphs were
    // once created by five hopeful clicks on a button that never said it had
    // already worked.
    if (template.installed_resource_id) {
      const confirmed = await this.dialogs.confirm(
        'Install another copy',
        `"${template.name}" is already installed${template.installed_count > 1 ? ` (${template.installed_count} copies)` : ''}. Install another separate copy?`,
        'Install another copy',
      );
      if (!confirmed) return;
    }
    this.busy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      const presetId = template.mechanism === 'identity_pass' ? this.presetId : '';
      const choices = Object.entries(this.assetChoices[template.id] ?? {})
        .filter(([, value]) => value)
        .map(([key, value]) => {
          const [node_id, input_name] = key.split(':');
          return { node_id: node_id ?? '', input_name: input_name ?? '', value };
        });
      await this.client.installWorkflowTemplate(template.id, modelId, '', presetId, choices);
      this.appState.settingsSavedAt = Date.now();
      await this.refreshCatalog();
      this.loadedModelId = '';
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'The workflow template could not be added.');
    } finally {
      this.busy = false;
      this.renderApp();
    }
  }
}
