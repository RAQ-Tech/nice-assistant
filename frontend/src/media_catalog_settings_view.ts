import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { CatalogModelsView } from './catalog_models_view';
import { IdentityWorkflowSetupView } from './identity_workflow_setup_view';
import { ModelPageView } from './model_page_view';
import { RecipePageView } from './recipe_page_view';
import { RecipeToolsView } from './recipe_tools_view';
import { ResourcePageView } from './resource_page_view';
import { WorkflowImportView } from './workflow_import_view';
import { VideoTemplateOffer } from './video_template_offer';
import { WorkflowTemplateView } from './workflow_template_view';
import { RoutingTesterView } from './routing_tester_view';
import { StarterPresetsView } from './starter_presets_view';
import type { SettingsDialogs } from './settings_contracts';
import {
  actionRow,
  choiceField,
  groupTitle,
  numberField,
  pageHint,
  pageNav,
  textField,
  thingList,
  type ThingChip,
} from './settings_page';
import { advancedSettings, settingsCard, titleCase } from './settings_ui';
import type {
  AppState,
  MediaCatalogResource,
  MediaPlanRequirements,
  MediaResourceType,
} from './types';

/** The two pages in this section that are not a thing in the catalog. */
export const NEW_WORKFLOW = 'new-workflow';
export const IDENTITY_CONTROL = 'identity-control';

/**
 * The Media Catalog: a list of plain things, each opening a page of its own.
 *
 * Models, recipes, workflows and LoRAs are the four kinds of thing here, and
 * every one of them opens to a page in the model page's shape. The page says
 * one line out loud - whether pictures can be made at all - and everything an
 * operator reaches for rarely sits behind one fold. A thing's address is
 * `#/settings/Media Catalog/<id>`, so it can be linked to and returned to.
 */
export class MediaCatalogSettingsView {
  async refresh(): Promise<void> {
    const settingsVersionAtStart = this.settingsVersion;
    const resourceVersionsAtStart = new Map(this.resourceVersions);
    this.appState.mediaCatalogBusy = true;
    try {
      const catalog = await this.client.mediaCatalog();
      void this.tools.refresh();
      void this.recipePage.refresh().then(() => this.renderApp());
      this.appState.mediaCatalog = this.mergeSnapshot(catalog, settingsVersionAtStart, resourceVersionsAtStart);
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Unable to load the media catalog.');
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private settingsDirty = false;
  private recipesRequested = false;
  private identityShown = false;
  private operatorToolsOpen = false;
  private settingsVersion = 0;
  private readonly dirtyResourceIds = new Set<string>();
  private readonly resourceVersions = new Map<string, number>();
  private readonly identitySetup: IdentityWorkflowSetupView;
  private readonly routingTester: RoutingTesterView;
  private readonly tools: RecipeToolsView;
  private readonly recipePage: RecipePageView;
  private readonly resourcePage: ResourcePageView;
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
    private readonly navigate: (item: string | null) => void = (item) => {
      appState.settingsItem = item;
      renderApp();
    },
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
    this.tools = new RecipeToolsView(appState, client, renderApp, () => this.refresh());
    this.recipePage = new RecipePageView(appState, client, renderApp, dialogs, (item) => this.navigate(item));
    this.resourcePage = new ResourcePageView(
      appState,
      client,
      renderApp,
      dialogs,
      (item) => this.navigate(item),
      (resource) => this.saveResource(resource),
      (resource) => this.deleteResource(resource),
    );
    this.starterPresets = new StarterPresetsView(appState, client, renderApp, () => this.refresh());
    this.modelsView = new CatalogModelsView(appState, client, renderApp, () => this.refresh(), (modelId) => this.navigate(modelId), dialogs);
    this.importView = new WorkflowImportView(
      appState,
      client,
      renderApp,
      () => this.refresh(),
      new VideoTemplateOffer(new WorkflowTemplateView(renderApp, appState, client, () => this.refresh(), dialogs), renderApp),
    );
    this.modelPage = new ModelPageView(appState, client, renderApp, dialogs, () => this.refresh(), (modelId) => this.navigate(modelId));
  }

  openIdentitySetup(): void {
    this.identitySetup.open();
    this.navigate(IDENTITY_CONTROL);
  }

  nodes(item: string | null = null): HTMLElement[] {
    const catalog = this.appState.mediaCatalog;
    if (!catalog) {
      return [
        settingsCard([
          pageHint('The catalog could not be loaded, so nothing about pictures can be inspected or changed.', 'catalog-unavailable'),
          actionRow([el('button', { class: 'pill-btn', textContent: 'Retry', onclick: () => void this.refresh() })]),
        ]),
      ];
    }
    // Arriving by address skips the section's refresh, so the recipes are
    // asked for here the first time they are needed.
    if (!this.recipePage.loaded && !this.recipesRequested) {
      this.recipesRequested = true;
      void this.recipePage.refresh().then(() => this.renderApp());
    }
    if (item !== IDENTITY_CONTROL) this.identityShown = false;
    if (item) return this.page(item, catalog.resources);
    return this.list(catalog.resources);
  }

  /** Leave only with intact work, whichever page is open. */
  beforeLeave(go: () => void): void {
    const item = this.appState.settingsItem;
    if (item && this.modelPage.modelId === item) {
      void this.modelPage.close(go);
      return;
    }
    if (item && this.resourcePage.resourceId === item) {
      this.resourcePage.beforeLeave(go);
      return;
    }
    if (item && this.recipePage.presetId === item) {
      this.recipePage.beforeLeave(go);
      return;
    }
    go();
  }

  /** What an address names: a model, a recipe, a workflow or LoRA, or one of the two named pages. */
  private page(item: string, resources: MediaCatalogResource[]): HTMLElement[] {
    const back = pageNav({ back: 'Media Catalog', onBack: () => this.navigate(null), testId: 'catalog-page' });
    if (item === NEW_WORKFLOW) {
      return [back, this.importView.node(resources.filter((entry) => entry.resource_type === 'model' && entry.enabled))];
    }
    if (item === IDENTITY_CONTROL) {
      // Reached by its address, the guide opens rather than showing a closed
      // summary - once, so its own toggle still works afterwards.
      if (!this.identityShown) {
        this.identityShown = true;
        this.identitySetup.open();
      }
      return [back, this.identitySetup.node()];
    }
    const resource = resources.find((entry) => entry.id === item);
    if (resource?.resource_type === 'model') {
      if (this.modelPage.modelId !== item) this.modelPage.open(item);
      return [this.modelPage.node(() => this.navigate(null))];
    }
    if (resource) {
      if (this.resourcePage.resourceId !== item) this.resourcePage.open(item);
      return [this.resourcePage.node()];
    }
    if (this.recipePage.knows(item)) {
      if (this.recipePage.presetId !== item) this.recipePage.open(item);
      return [this.recipePage.node()];
    }
    if (!this.recipePage.loaded) return [back, settingsCard([pageHint('Opening…', 'catalog-page-opening')])];
    return [back, settingsCard([pageHint('Nothing in the catalog has that address.', 'catalog-page-missing')])];
  }

  private list(resources: MediaCatalogResource[]): HTMLElement[] {
    const models = resources.filter((entry) => entry.resource_type === 'model' && entry.enabled);
    const workflows = resources.filter((entry) => entry.resource_type === 'workflow');
    const loras = resources.filter((entry) => entry.resource_type === 'lora');
    const recipes = this.recipePage.list();
    const identityReady = workflows.some((entry) =>
      entry.enabled && entry.features.includes('identity_control') && entry.compatible_model_ids.length > 0);
    const chip = (entry: MediaCatalogResource, kind: string): ThingChip => ({
      id: entry.id,
      label: entry.name,
      note: entry.enabled ? undefined : 'off',
      title: entry.external_id,
      testId: `catalog-${kind}-open-${entry.id}`,
      onOpen: () => this.navigate(entry.id),
    });
    const readiness = models.length === 0
      ? 'Nothing can be generated until a model is enabled. Find models on ComfyUI below.'
      : models.length === 1
        ? 'One model means every picture shares its look. Add more from ComfyUI, and open one to say when to use it.'
        : `${models.length} models, ${workflows.filter((entry) => entry.enabled).length} workflows and ${recipes.filter((entry) => entry.enabled).length} recipes are enabled.`;
    return [
      settingsCard([
        pageHint(readiness, 'catalog-readiness'),
        this.modelsView.node(resources.filter((entry) => entry.resource_type === 'model' && entry.kind === 'image')),
        groupTitle(`Recipes (${recipes.length})`, 'A recipe pairs a model with a workflow and its numbers, and its note says when to use it. Chats choose between recipes.', 'catalog-recipes'),
        thingList(recipes.map((preset) => ({
          id: preset.id,
          label: preset.name,
          note: preset.enabled ? undefined : 'off',
          title: preset.routing_card || 'No note yet - open it to say when to use it.',
          testId: `catalog-recipe-open-${preset.id}`,
          onOpen: () => this.navigate(preset.id),
        })), 'No recipes yet. Every model added from ComfyUI gets one.', 'catalog-recipe-list'),
        groupTitle(`Workflows (${workflows.length})`, 'The method: plain generation, identity conditioning, face swap, correction. A recipe runs its model through one.', 'catalog-workflows'),
        thingList(workflows.map((entry) => chip(entry, 'workflow')), 'No workflows yet. Add one below, or start from a shipped graph.', 'catalog-workflow-list'),
        actionRow([
          el('button', {
            class: 'pill-btn',
            textContent: 'Add a workflow',
            title: 'Paste a workflow exported from ComfyUI in API format, or start from a shipped graph.',
            'data-testid': 'catalog-new-workflow',
            onclick: () => this.navigate(NEW_WORKFLOW),
          }),
          el('button', {
            class: 'pill-btn',
            textContent: identityReady ? 'Identity control · configured' : 'Identity control · not set up',
            title: 'A workflow that keeps a persona recognisable from a photo, checked against ComfyUI before it is saved.',
            'data-testid': 'catalog-identity-control',
            onclick: () => this.openIdentitySetup(),
          }),
        ]),
        groupTitle(`LoRAs (${loras.length})`, 'A LoRA leans a compatible model and joins a recipe when its metadata matches the request.', 'catalog-loras'),
        thingList(loras.map((entry) => chip(entry, 'lora')), 'No LoRAs yet.', 'catalog-lora-list'),
      ]),
      advancedSettings(
        'More options',
        'Operator tools: starter recipes, coordinator limits, manual adds, recipe files, routing tests and plan previews.',
        [
          this.starterPresets.node(),
          this.policyCard(),
          settingsCard([
            groupTitle('Add by hand', 'For a file ComfyUI does not list, or a resource of another kind.'),
            actionRow([
              el('button', { class: 'pill-btn', textContent: 'Add model manually', onclick: () => void this.addResource('model') }),
              el('button', { class: 'pill-btn', textContent: 'Add LoRA', onclick: () => void this.addResource('lora') }),
              el('button', { class: 'pill-btn', textContent: 'Add workflow', onclick: () => void this.addResource('workflow') }),
              el('button', { class: 'pill-btn', textContent: 'Refresh catalog', onclick: () => void this.refresh() }),
            ]),
          ]),
          ...this.tools.nodes(),
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
      groupTitle('Coordinator limits', 'Planning limits keep one request from selecting an unbounded estimated VRAM load or LoRA chain.'),
      el('div', { class: 'settings-grid' }, [
        numberField('Shared VRAM budget (MB)', String(catalog.settings.vram_budget_mb), (value) => {
          catalog.settings.vram_budget_mb = boundedInteger(value, 0, 131072, catalog.settings.vram_budget_mb);
          this.markSettingsDirty();
        }, { hover: '0 turns the catalog estimate limit off. Live GPU admission checks still run.' }),
        numberField('Maximum selected LoRAs', String(catalog.settings.max_loras), (value) => {
          catalog.settings.max_loras = boundedInteger(value, 0, 8, catalog.settings.max_loras);
          this.markSettingsDirty();
        }, { hover: 'How many compatible LoRAs one plan may select.' }),
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

  private planPreview(): HTMLElement {
    const plan = this.appState.mediaPlanPreview;
    return settingsCard([
      groupTitle('Preview coordinator selection', 'Tests deterministic metadata selection without storing or sending prompt content. Nothing is generated.'),
      el('div', { class: 'settings-grid' }, [
        choiceField('Media kind', this.requirements.kind, ['image', 'video'], (value) => {
          this.requirements.kind = value as 'image' | 'video';
        }, { display: titleCase, hover: 'Limits selection to image or video resources.' }),
        choiceField('Operation', this.requirements.operation, ['generate', 'inpaint', 'outpaint', 'image_to_image'], (value) => {
          this.requirements.operation = value as MediaPlanRequirements['operation'];
        }, { display: titleCase, hover: 'A hard requirement. Editing operations also need exact protected source and mask bindings when they run.' }),
        textField('Preferred domains', this.requirements.domains.join(', '), (value) => { this.requirements.domains = tagList(value); }, { hover: 'Soft strengths such as fantasy, portrait, or photorealism.' }),
        textField('Required content tags', this.requirements.content_tags.join(', '), (value) => { this.requirements.content_tags = tagList(value); }, { hover: 'Content categories the selected resources must explicitly support.' }),
        textField('Required features', this.requirements.required_features.join(', '), (value) => { this.requirements.required_features = tagList(value); }, { hover: 'Hard features such as identity_control.' }),
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

  private async saveResource(resource: MediaCatalogResource): Promise<boolean> {
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
      return true;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, `Unable to save ${resource.name}.`);
      return false;
    } finally {
      this.appState.mediaCatalogBusy = false;
      this.renderApp();
    }
  }

  private async deleteResource(resource: MediaCatalogResource): Promise<boolean> {
    if (!(await this.dialogs.confirm('Delete media resource', `Delete ${resource.name}? Existing plans remain auditable but cannot run.`, 'Delete'))) return false;
    this.appState.mediaCatalogBusy = true;
    this.renderApp();
    try {
      await this.client.deleteMediaCatalogResource(resource.id);
      this.dirtyResourceIds.delete(resource.id);
      this.resourceVersions.set(resource.id, (this.resourceVersions.get(resource.id) ?? 0) + 1);
      this.appState.mediaCatalog = this.mergeSnapshot(await this.client.mediaCatalog());
      return true;
    } catch (error) {
      this.appState.settingsError = errorMessage(error, `Unable to delete ${resource.name}.`);
      return false;
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
