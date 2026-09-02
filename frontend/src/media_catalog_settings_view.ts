import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { CatalogModelsView } from './catalog_models_view';
import { IdentityWorkflowSetupView } from './identity_workflow_setup_view';
import { ModelPageView } from './model_page_view';
import { WorkflowImportView } from './workflow_import_view';
import { VideoTemplateOffer } from './video_template_offer';
import { WorkflowTemplateView } from './workflow_template_view';
import { PresetSettingsView } from './preset_settings_view';
import { RoutingTesterView } from './routing_tester_view';
import { StarterPresetsView } from './starter_presets_view';
import { inputField, selectField, textareaField, toggleField } from './settings_controls';
import type { SettingsDialogs } from './settings_contracts';
import { advancedSettings, operatorEditor, readinessRow, settingsCard, settingsHeading, settingsIntro, titleCase } from './settings_ui';
import type {
  AppState,
  MediaCatalogResource,
  MediaPlanRequirements,
  MediaResourceType,
} from './types';

export class MediaCatalogSettingsView {
  async refresh(): Promise<void> {
    const settingsVersionAtStart = this.settingsVersion;
    const resourceVersionsAtStart = new Map(this.resourceVersions);
    this.appState.mediaCatalogBusy = true;
    try {
      const catalog = await this.client.mediaCatalog();
      void this.presets.refresh();
      this.appState.mediaCatalog = this.mergeSnapshot(catalog, settingsVersionAtStart, resourceVersionsAtStart);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to load the media catalog.');
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private settingsDirty = false;
  private operatorToolsOpen = false;
  private settingsVersion = 0;
  private readonly dirtyResourceIds = new Set<string>();
  private readonly resourceVersions = new Map<string, number>();
  private readonly openResourceIds = new Set<string>();
  private readonly identitySetup: IdentityWorkflowSetupView;
  private readonly routingTester: RoutingTesterView;
  private readonly presets: PresetSettingsView;
  private readonly starterPresets: StarterPresetsView;
  private readonly modelsView: CatalogModelsView;
  private readonly importView: WorkflowImportView;
  private readonly modelPage: ModelPageView;
  private requirements: MediaPlanRequirements = {
    kind: 'image',
    operation: 'generate',
    domains: [],
    content_tags: [],
    required_features: [],
  };
  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly dialogs: SettingsDialogs,
    private readonly finishIdentitySetup: () => void = () => undefined,
  ) {
    this.identitySetup = new IdentityWorkflowSetupView(
      renderApp,
      appState,
      client,
      dialogs,
      finishIdentitySetup,
      () => this.refresh(),
    );
    this.routingTester = new RoutingTesterView(appState, client, renderApp);
    this.presets = new PresetSettingsView(appState, client, renderApp);
    this.starterPresets = new StarterPresetsView(appState, client, renderApp, () => this.refresh());
    this.modelsView = new CatalogModelsView(appState, client, renderApp, () => this.refresh(), (modelId) => {
      this.modelPage.open(modelId);
      renderApp();
    });
    this.importView = new WorkflowImportView(
      appState,
      client,
      renderApp,
      () => this.refresh(),
      new VideoTemplateOffer(new WorkflowTemplateView(renderApp, appState, client, () => this.refresh(), dialogs), renderApp),
    );
    this.modelPage = new ModelPageView(appState, client, renderApp, dialogs, () => this.refresh());
  }

  openIdentitySetup(): void {
    this.identitySetup.open();
  }

  nodes(): HTMLElement[] {
    const catalog = this.appState.mediaCatalog;
    if (!catalog) {
      return [
        settingsIntro(
          'Teach the media coordinator what is available',
          'The catalog is currently unavailable, so Nice Assistant cannot inspect or change coordinated media resources.',
        ),
        el('div', { class: 'settings-empty-state', textContent: 'No media catalog was returned. Retry the request, then check server logs if it remains unavailable.' }),
        el('button', { class: 'pill-btn', textContent: 'Retry catalog', onclick: () => void this.refresh() }),
      ];
    }
    // One model at a time: the open model replaces the catalog rather than
    // stacking on top of it, which is the whole point of the page.
    if (this.modelPage.modelId) return [this.modelPage.node(() => this.renderApp())];
    const enabled = catalog.resources.filter((item) => item.enabled);
    const models = enabled.filter((item) => item.resource_type === 'model');
    const workflows = enabled.filter((item) => item.resource_type === 'workflow');
    const loras = enabled.filter((item) => item.resource_type === 'lora');
    const allImageModels = catalog.resources.filter((item) => item.resource_type === 'model' && item.kind === 'image');
    return [
      settingsIntro(
        'How pictures get made',
        'A chat picks one of your presets. A preset pairs a model (the look) with a workflow (the method), '
          + 'and the note on each preset is how the chat knows when to pick it. More presets means more variety.',
      ),
      el('div', { class: 'settings-readiness-list' }, [
        readinessRow(
          'Models',
          `${models.length} enabled`,
          models.length > 1 ? 'ready' : 'attention',
          models.length === 0
            ? 'Nothing can be generated until a model is enabled. Find models below.'
            : models.length === 1
              ? 'One model means every picture shares its look. Find more below.'
              : 'Each enabled model gives its recipes a different look.',
        ),
        readinessRow('Workflows', `${workflows.length} enabled`, workflows.length ? 'ready' : 'off', 'A workflow is the method: plain generation, identity conditioning, face swap, correction.'),
        readinessRow('LoRAs', `${loras.length} enabled`, loras.length ? 'ready' : 'off', 'LoRAs adjust a compatible model and join a recipe when their metadata matches the request.'),
      ]),
      this.modelsView.node(allImageModels),
      settingsCard([
        settingsHeading(
          'Workflows — the method',
          'Start from a shipped, known-good workflow below, or paste your own export. '
            + 'Identity workflows keep a persona recognisable; the guided setup walks that end to end.',
        ),
      ]),
      this.identitySetup.node(),
      this.importView.node(models),
      advancedSettings(
        'Operator tools',
        'The machinery behind the sections above: the raw recipe list, inventory, manual adds, starter bundles, limits, and planner diagnostics.',
        [
          // The raw recipe list lives here now: models are the front door for
          // day-to-day settings, and this remains the place for multi-recipe
          // and diagnostic work.
          ...this.presets.node(),
          settingsCard([
            settingsHeading(
              `Inventory (${catalog.resources.length})`,
              'Every cataloged part. Open one to edit compatibility, strengths, estimates, or the provider payload — or to delete duplicate copies.',
            ),
            el('div', { class: 'chips' }, [
              el('button', { class: 'pill-btn', textContent: 'Add model manually', onclick: () => void this.addResource('model') }),
              el('button', { class: 'pill-btn', textContent: 'Add LoRA', onclick: () => void this.addResource('lora') }),
              el('button', { class: 'pill-btn', textContent: 'Add workflow', onclick: () => void this.addResource('workflow') }),
              el('button', { class: 'pill-btn', textContent: 'Refresh catalog', onclick: () => void this.refresh() }),
            ]),
            ...(catalog.resources.length
              ? catalog.resources.map((resource) => this.resourceCard(resource))
              : [el('div', {
                  class: 'settings-empty-state',
                  textContent: 'Nothing is cataloged yet. Find models on ComfyUI above to begin.',
                })]),
          ]),
          this.policyCard(),
          this.starterPresets.node(),
          this.routingTester.node(),
          this.planPreview(),
        ],
        {
          testId: 'catalog-inventory',
          // Held open across re-renders: every control inside triggers a
          // render, and a fold that snapped shut on each click would hide the
          // buttons somebody was in the middle of using.
          open: this.operatorToolsOpen,
          onToggle: (open: boolean) => {
            this.operatorToolsOpen = open;
          },
        },
      ),
    ];
  }

  private policyCard(): HTMLElement {
    const catalog = this.appState.mediaCatalog;
    if (!catalog) return el('div');
    return settingsCard([
      settingsHeading('Coordinator limits', 'Planning limits prevent a single request from selecting an unbounded estimated VRAM load or LoRA chain.'),
      el('div', { class: 'settings-grid' }, [
        inputField('Shared VRAM budget (MB)', String(catalog.settings.vram_budget_mb), (value) => {
          catalog.settings.vram_budget_mb = boundedInteger(value, 0, 131072, catalog.settings.vram_budget_mb);
          this.markSettingsDirty();
        }, 'number', false, 'Set 0 to disable the catalog estimate limit. This does not disable live GPU admission checks.'),
        inputField('Maximum selected LoRAs', String(catalog.settings.max_loras), (value) => {
          catalog.settings.max_loras = boundedInteger(value, 0, 8, catalog.settings.max_loras);
          this.markSettingsDirty();
        }, 'number', false, 'Caps how many compatible LoRAs one coordinated plan may select.'),
      ]),
      el('button', {
        class: 'send-btn',
        textContent: this.appState.mediaCatalogBusy ? 'Saving…' : 'Save coordinator limits',
        disabled: this.appState.mediaCatalogBusy,
        'data-testid': 'media-catalog-save-policy',
        onclick: () => void this.saveSettings(),
      }),
    ]);
  }

  private resourceCard(resource: MediaCatalogResource): HTMLElement {
    const models = (this.appState.mediaCatalog?.resources ?? []).filter((item) =>
      item.resource_type === 'model'
      && item.kind === resource.kind
      && item.provider_key === resource.provider_key
      && item.backend === resource.backend
      && item.id !== resource.id
    );
    // Video is local-only by decision; an existing cloud row stays renderable
    // but new video resources are ComfyUI's.
    const providerOptions = resource.kind === 'video'
      ? [...new Set(['local-video', resource.provider_key])]
      : (resource.resource_type === 'model' ? ['local-image', 'openai-image'] : ['local-image']);
    const backendOptions = resource.provider_key === 'local-image'
      ? (resource.resource_type === 'workflow' ? ['comfyui'] : ['automatic1111', 'comfyui'])
      : resource.provider_key === 'local-video'
        ? ['comfyui']
        : ['openai'];
    const compatible = models.filter((model) => resource.compatible_model_ids.includes(model.id));
    const status = resource.enabled ? 'Enabled' : 'Draft';
    const subtitle = `${titleCase(resource.resource_type)} · ${resource.backend} · revision ${resource.revision}`;
    return operatorEditor(
      resource.name || 'Unnamed resource',
      subtitle,
      status,
      [
        toggleField('Enabled for planning', resource.enabled, (value) => { resource.enabled = value; }, 'Disabled resources remain editable drafts and are never selected for new plans.'),
        el('div', { class: 'settings-grid' }, [
          inputField('Name', resource.name, (value) => { resource.name = value; }, 'text', false, 'Human-readable name shown in plans and operator pickers.'),
          selectField('Resource type', resource.resource_type, ['model', 'lora', 'workflow'], (value) => {
            resource.resource_type = value as MediaResourceType;
          }, undefined, titleCase, false, 'Models generate; LoRAs specialize a compatible model; workflows declare an executable ComfyUI operation.'),
          selectField('Media kind', resource.kind, ['image', 'video'], (value) => {
            resource.kind = value as 'image' | 'video';
          }, undefined, titleCase, false, 'The output family this resource can produce or modify.'),
          selectField('Provider adapter', resource.provider_key, providerOptions, (value) => {
            resource.provider_key = value as MediaCatalogResource['provider_key'];
          }, undefined, titleCase, false, 'The Nice Assistant provider contract used to execute this resource.'),
          selectField('Backend', resource.backend, backendOptions, (value) => {
            resource.backend = value as MediaCatalogResource['backend'];
          }, undefined, titleCase, false, 'The actual service that owns the model, LoRA, or workflow.'),
          inputField(
            resource.resource_type === 'workflow' ? 'Catalog workflow ID' : 'Provider resource ID or filename',
            resource.external_id,
            (value) => { resource.external_id = value; },
            'text',
            false,
            resource.resource_type === 'workflow'
              ? 'An operator-facing catalog identifier. The executable workflow patch is stored in Advanced resource metadata.'
              : 'Exact provider identifier or checkpoint filename. Selection still depends on explicit metadata, not this name.',
          ),
        ]),
        resource.resource_type !== 'model'
          ? el('div', { class: 'compatibility-list' }, [
              settingsHeading(
                'Compatible base models',
                'Only checked models can be paired with this resource. Compatibility is exact and same-provider/backend.',
                'strong',
              ),
              models.length
                ? el('div', { class: 'chips' }, models.map((model) =>
                    el('label', { class: 'checkbox-row' }, [
                      el('input', {
                        type: 'checkbox',
                        checked: resource.compatible_model_ids.includes(model.id),
                        onchange: (event: Event) => {
                          const checked = (event.currentTarget as HTMLInputElement).checked;
                          resource.compatible_model_ids = checked
                            ? [...new Set([...resource.compatible_model_ids, model.id])]
                            : resource.compatible_model_ids.filter((id) => id !== model.id);
                        },
                      }),
                      model.name,
                    ]),
                  ))
                : el('div', { class: 'settings-empty-state', textContent: 'No same-provider, same-backend base model is available.' }),
              compatible.length
                ? el('span', { class: 'meta', textContent: `${compatible.length} compatible model${compatible.length === 1 ? '' : 's'} selected` })
                : null,
            ])
          : null,
        advancedSettings(
          'Selection metadata and provider payload',
          'Expert metadata used by deterministic planning and provider execution.',
          [
            el('div', { class: 'settings-grid' }, [
              inputField('Priority (0–100)', String(resource.priority), (value) => {
                resource.priority = boundedInteger(value, 0, 100, resource.priority);
              }, 'number', false, 'Breaks otherwise equal compatible choices; higher numbers are preferred.'),
              inputField('Estimated VRAM (MB)', String(resource.estimated_vram_mb), (value) => {
                resource.estimated_vram_mb = boundedInteger(value, 0, 131072, resource.estimated_vram_mb);
              }, 'number', false, 'Operator estimate used by planning. Set 0 when unknown rather than inventing capacity.'),
              inputField('Estimated load seconds', String(resource.estimated_load_seconds), (value) => {
                resource.estimated_load_seconds = boundedNumber(value, 0, 3600, resource.estimated_load_seconds);
              }, 'number', false, 'Expected cold-load time used for explanation and future scheduling decisions.'),
              inputField('Operations', resource.operations.join(', '), (value) => {
                resource.operations = tagList(value) as MediaCatalogResource['operations'];
              }, 'text', false, 'Comma-separated executable operations such as generate, inpaint, outpaint, or image_to_image.'),
              inputField('Domain strengths', resource.domains.join(', '), (value) => { resource.domains = tagList(value); }, 'text', false, 'Comma-separated subject strengths such as fantasy, portrait, or photorealism.'),
              inputField('Content strengths', resource.content_tags.join(', '), (value) => { resource.content_tags = tagList(value); }, 'text', false, 'Comma-separated content categories that this resource is intentionally allowed and suited to handle.'),
              inputField('Features', resource.features.join(', '), (value) => { resource.features = tagList(value); }, 'text', false, 'Hard capability flags such as text_to_image or identity_control.'),
            ]),
            textareaField('Default settings JSON', JSON.stringify(resource.default_settings, null, 2), (value) => {
              try {
                const parsed = JSON.parse(value || '{}');
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) resource.default_settings = parsed;
                this.appState.settingsError = '';
              } catch {
                this.appState.settingsError = `Default settings for ${resource.name || 'resource'} are not valid JSON.`;
              }
            }, false, 'Provider-specific defaults. Invalid or unsupported values can block execution.'),
            resource.resource_type === 'workflow'
              ? el('div', {
                  class: 'settings-warning',
                  textContent: 'Workflows stay safest as disabled drafts until their inline patch, exact bindings, compatible base model, and declared features have been reviewed.',
                })
              : null,
            textareaField('Operator notes', resource.notes, (value) => { resource.notes = value; }, false, 'Private operational context for future catalog maintenance.'),
          ],
          { testId: `media-resource-advanced-${resource.id}` },
        ),
        el('div', { class: 'operator-actions' }, [
          el('button', {
            class: 'send-btn',
            textContent: this.appState.mediaCatalogBusy ? 'Saving…' : 'Save resource',
            disabled: this.appState.mediaCatalogBusy,
            'data-testid': `media-resource-save-${resource.id}`,
            onclick: () => void this.saveResource(resource),
          }),
          el('button', {
            class: 'pill-btn danger',
            textContent: 'Delete resource',
            disabled: this.appState.mediaCatalogBusy,
            onclick: () => void this.deleteResource(resource),
          }),
        ]),
      ],
      {
        open: this.openResourceIds.has(resource.id),
        onToggle: (open) => open ? this.openResourceIds.add(resource.id) : this.openResourceIds.delete(resource.id),
        testId: `media-resource-${resource.id}`,
        className: 'media-resource-card',
        statusClass: resource.enabled ? 'ok' : 'idle',
        onInput: () => this.markResourceDirty(resource.id),
        onChange: () => this.markResourceDirty(resource.id),
      },
    );
  }

  private planPreview(): HTMLElement {
    const plan = this.appState.mediaPlanPreview;
    return settingsCard([
      settingsHeading(
        'Preview coordinator selection',
        'Tests deterministic metadata selection without storing or sending prompt content. This does not generate media.',
      ),
      el('div', { class: 'settings-grid' }, [
        selectField('Media kind', this.requirements.kind, ['image', 'video'], (value) => {
          this.requirements.kind = value as 'image' | 'video';
        }, undefined, titleCase, false, 'Limits selection to image or video resources.'),
        selectField('Operation', this.requirements.operation, ['generate', 'inpaint', 'outpaint', 'image_to_image'], (value) => {
          this.requirements.operation = value as MediaPlanRequirements['operation'];
        }, undefined, titleCase, false, 'Hard operation requirement. Editing operations also require exact protected source/mask bindings at execution time.'),
        inputField('Preferred domains', this.requirements.domains.join(', '), (value) => { this.requirements.domains = tagList(value); }, 'text', false, 'Soft semantic strengths such as fantasy, portrait, or photorealism.'),
        inputField('Required content tags', this.requirements.content_tags.join(', '), (value) => { this.requirements.content_tags = tagList(value); }, 'text', false, 'Content categories the selected resources must explicitly support.'),
        inputField('Required features', this.requirements.required_features.join(', '), (value) => { this.requirements.required_features = tagList(value); }, 'text', false, 'Hard features such as identity_control.'),
      ]),
      el('button', {
        class: 'pill-btn',
        textContent: this.appState.mediaCatalogBusy ? 'Planning…' : 'Preview selection',
        disabled: this.appState.mediaCatalogBusy,
        'data-testid': 'media-plan-preview',
        onclick: () => void this.previewPlan(),
      }),
      plan
        ? el('div', { class: `media-plan-preview plan-${plan.status}` }, [
            el('strong', { textContent: `${titleCase(plan.status)} · ${plan.estimated_vram_mb || 'unknown'} MB estimated VRAM` }),
            el('p', { textContent: plan.block?.message || plan.explanation.summary }),
            plan.selected_resources.length
              ? el('ul', {}, plan.selected_resources.map((item) => el('li', { textContent: `${titleCase(item.resource_type)}: ${item.name}` })))
              : null,
            plan.identity_conditioning
              ? el('div', {
                  class: 'meta',
                  textContent: plan.identity_conditioning.status === 'ready'
                    ? 'Persona reference conditioning is ready; configured validation runs after generation and may trigger bounded correction attempts.'
                    : 'A real persona chat and active reviewed reference are required before this identity workflow can run.',
                })
              : null,
            ...plan.explanation.warnings.map((warning) => el('div', { class: 'meta', textContent: warning })),
          ])
        : el('div', { class: 'meta', textContent: 'No preview has been run with the current requirements.' }),
    ]);
  }

  private markSettingsDirty(): void {
    this.settingsDirty = true;
    this.settingsVersion += 1;
  }

  private markResourceDirty(resourceId: string): void {
    this.dirtyResourceIds.add(resourceId);
    this.resourceVersions.set(resourceId, (this.resourceVersions.get(resourceId) ?? 0) + 1);
  }

  private mergeSnapshot(
    incoming: NonNullable<AppState['mediaCatalog']>,
    settingsVersionAtStart = this.settingsVersion,
    resourceVersionsAtStart = new Map(this.resourceVersions),
  ): NonNullable<AppState['mediaCatalog']> {
    const current = this.appState.mediaCatalog;
    if (!current) return incoming;
    const preserveSettings = this.settingsDirty || this.settingsVersion !== settingsVersionAtStart;
    const currentResources = new Map(current.resources.map((resource) => [resource.id, resource]));
    const incomingIds = new Set(incoming.resources.map((resource) => resource.id));
    const resources = incoming.resources.map((resource) => {
      const currentResource = currentResources.get(resource.id);
      const changedWhileLoading = (this.resourceVersions.get(resource.id) ?? 0)
        !== (resourceVersionsAtStart.get(resource.id) ?? 0);
      return currentResource && (this.dirtyResourceIds.has(resource.id) || changedWhileLoading)
        ? currentResource
        : resource;
    });
    resources.push(...current.resources.filter((resource) => this.dirtyResourceIds.has(resource.id) && !incomingIds.has(resource.id)));
    return { ...incoming, settings: preserveSettings ? current.settings : incoming.settings, resources };
  }

  private async saveSettings(): Promise<void> {
    const catalog = this.appState.mediaCatalog;
    if (!catalog) return;
    this.appState.mediaCatalogBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      catalog.settings = await this.client.updateMediaCatalogSettings(catalog.settings);
      this.settingsVersion += 1;
      this.settingsDirty = false;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to save the media catalog policy.');
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private async addResource(resourceType: MediaResourceType): Promise<void> {
    const catalog = this.appState.mediaCatalog;
    if (!catalog) return;
    const name = await this.dialogs.prompt(`Add ${resourceType}`, 'Choose the operator-facing resource name.');
    if (!name?.trim()) return;
    const externalId = await this.dialogs.prompt(
      `Add ${resourceType}`,
      resourceType === 'workflow' ? 'Choose a catalog workflow ID. The executable patch is reviewed after creation.' : 'Enter the exact provider model or resource ID.',
      resourceType === 'model' ? 'provider-default' : '',
    );
    if (!externalId?.trim()) return;
    let backend: MediaCatalogResource['backend'] = 'automatic1111';
    let compatibleModelIds: string[] = [];
    if (resourceType !== 'model') {
      const candidates = catalog.resources.filter((item) =>
        item.resource_type === 'model'
        && item.kind === 'image'
        && item.provider_key === 'local-image'
        && (resourceType !== 'workflow' || item.backend === 'comfyui')
      );
      if (!candidates.length) {
        this.dialogs.info(
          'Base model required',
          resourceType === 'workflow'
            ? 'Add a ComfyUI base model before adding a workflow.'
            : 'Add a local image base model before adding a LoRA.',
        );
        return;
      }
      const firstCandidate = candidates[0];
      if (!firstCandidate) return;
      backend = resourceType === 'workflow' ? 'comfyui' : firstCandidate.backend;
      compatibleModelIds = [candidates.find((item) => item.backend === backend)?.id ?? firstCandidate.id];
    }
    this.appState.mediaCatalogBusy = true;
    this.renderApp();
    try {
      await this.client.createMediaCatalogResource({
        resource_type: resourceType,
        kind: 'image',
        name: name.trim(),
        provider_key: 'local-image',
        backend,
        external_id: externalId.trim(),
        enabled: resourceType !== 'workflow',
        priority: 50,
        operations: ['generate'],
        domains: [],
        content_tags: resourceType === 'model' ? ['general'] : [],
        features: resourceType === 'model' ? ['text_to_image'] : [],
        estimated_vram_mb: 0,
        estimated_load_seconds: 0,
        default_settings: resourceType === 'lora'
          ? { weight: 1, trigger_words: [] }
          : (resourceType === 'workflow' ? { workflow_patch: {} } : {}),
        notes: '',
        compatible_model_ids: compatibleModelIds,
      });
      this.appState.mediaCatalog = this.mergeSnapshot(await this.client.mediaCatalog());
    } catch (error) {
      this.appState.settingsError = errorMessage(error, `Unable to add ${resourceType}.`);
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private async saveResource(resource: MediaCatalogResource): Promise<void> {
    this.appState.mediaCatalogBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      const saved = await this.client.updateMediaCatalogResource(resource);
      if (this.appState.mediaCatalog) {
        this.appState.mediaCatalog.resources = this.appState.mediaCatalog.resources.map((item) => item.id === saved.id ? saved : item);
        this.resourceVersions.set(saved.id, (this.resourceVersions.get(saved.id) ?? 0) + 1);
        this.dirtyResourceIds.delete(saved.id);
        this.appState.mediaCatalog.vocabulary = (await this.client.mediaCatalog()).vocabulary;
      }
    } catch (error) {
      this.appState.settingsError = errorMessage(error, `Unable to save ${resource.name}.`);
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private async deleteResource(resource: MediaCatalogResource): Promise<void> {
    if (!(await this.dialogs.confirm('Delete media resource', `Delete ${resource.name}? Existing plans remain auditable but cannot run.`, 'Delete'))) return;
    this.appState.mediaCatalogBusy = true;
    this.renderApp();
    try {
      await this.client.deleteMediaCatalogResource(resource.id);
      this.dirtyResourceIds.delete(resource.id);
      this.resourceVersions.set(resource.id, (this.resourceVersions.get(resource.id) ?? 0) + 1);
      this.openResourceIds.delete(resource.id);
      this.appState.mediaCatalog = this.mergeSnapshot(await this.client.mediaCatalog());
    } catch (error) {
      this.appState.settingsError = errorMessage(error, `Unable to delete ${resource.name}.`);
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private async previewPlan(): Promise<void> {
    this.appState.mediaCatalogBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      this.appState.mediaPlanPreview = await this.client.previewMediaPlan(this.requirements);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to preview media selection.');
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }
}

function tagList(value: string): string[] {
  return [...new Set(value.split(',').map((item) => item.trim().toLowerCase()).filter(Boolean))];
}

function boundedInteger(value: string, minimum: number, maximum: number, fallback: number): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? Math.max(minimum, Math.min(maximum, parsed)) : fallback;
}

function boundedNumber(value: string, minimum: number, maximum: number, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(minimum, Math.min(maximum, parsed)) : fallback;
}
