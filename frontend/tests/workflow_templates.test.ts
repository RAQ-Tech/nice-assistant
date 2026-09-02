import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { createState } from '../src/state';
import type { WorkflowTemplate } from '../src/types';
import { WorkflowTemplateView } from '../src/workflow_template_view';

function template(overrides: Partial<WorkflowTemplate> = {}): WorkflowTemplate {
  return {
    id: 'photomaker-v2-sdxl',
    name: 'PhotoMaker v2 (SDXL)',
    template_version: 1,
    summary: 'Conditions generation on one approved reference photo.',
    kind: 'image',
    mechanism: 'reference_adapter',
    architectures: ['sdxl'],
    required_assets: ['photomaker-v2.bin in the ComfyUI models/photomaker folder'],
    required_prompt_token: 'photomaker',
    installed_resource_id: null,
    installed_version: null,
    installed_count: 0,
    update_available: false,
    architecture_matches: true,
    ...overrides,
  };
}

function setup(templates: WorkflowTemplate[], architecture = 'sdxl') {
  const appState = createState();
  const client = {
    workflowTemplates: vi.fn().mockResolvedValue({ model_id: 'model-1', model_architecture: architecture, templates }),
    verifyWorkflowTemplate: vi.fn().mockResolvedValue({
      provider: 'comfyui',
      status: 'incompatible',
      provider_compatible: false,
      live_tested: false,
      message: 'The workflow remains a draft.',
      identity_input_candidates: [],
      request_input_candidates: { prompt: [], seed: [], width: [], height: [], checkpoint: [] },
      detected_node_types: [],
      missing_node_types: ['PhotoMakerEncodeV2'],
      asset_checks: [{
        node_id: '2',
        node_type: 'PhotoMakerLoaderV2',
        input_name: 'photomaker_model_name',
        value: 'photomaker-v2.bin',
        available: false,
        options: ['photomaker-v1.bin', 'photomaker-v2-fp16.bin'],
      }],
      warnings: [],
    }),
    installWorkflowTemplate: vi.fn().mockResolvedValue({ id: 'workflow-9' }),
    mediaPresets: vi.fn().mockResolvedValue({ items: [{ id: 'preset-1', name: 'Portrait recipe', kind: 'image' }] }),
  } as unknown as ApiClient;
  const refreshCatalog = vi.fn().mockResolvedValue(undefined);
  const root = document.createElement('div');
  let view!: WorkflowTemplateView;
  const render = () => root.replaceChildren(view.node('model-1', 'RealVis XL'));
  view = new WorkflowTemplateView(render, appState, client, refreshCatalog, { confirm: async () => true, info: () => undefined } as never);
  render();
  // The panel loads on its first render, so every test waits for the cards
  // rather than for the call, which returns before the render that shows them.
  const loaded = () => vi.waitFor(() => expect(root.querySelector('.workflow-template-card')).not.toBeNull());
  return { appState, client, refreshCatalog, root, render, loaded };
}

describe('Workflow templates', () => {
  it('offers a shipped graph with what it needs, without claiming it has been run', async () => {
    const { client, root, loaded } = setup([template()]);
    await loaded();
    expect(client.workflowTemplates).toHaveBeenCalledWith('model-1');

    expect(root.textContent).toContain('PhotoMaker v2 (SDXL)');
    expect(root.textContent).toContain('Conditions generation on the reference');
    expect(root.textContent).toContain('photomaker-v2.bin');
    // The whole point of the panel: nothing here asks which input receives the
    // prompt, and nothing claims a picture has been made.
    expect(root.textContent).toContain('has been generation-tested on this deployment');
    expect(root.querySelector('[data-testid="workflow-template-photomaker-v2-sdxl"]')).not.toBeNull();
  });

  it('says what a check could not see rather than implying it checked everything', async () => {
    const { client, root, loaded } = setup([template()]);
    await loaded();

    (root.querySelector('[data-testid="workflow-template-verify-photomaker-v2-sdxl"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.verifyWorkflowTemplate).toHaveBeenCalledWith('photomaker-v2-sdxl'));

    expect(root.textContent).toContain('Not installed: PhotoMakerEncodeV2');
    expect(root.textContent).toContain('photomaker-v2.bin is not available');
    expect(root.textContent).toContain('picks by device rather than by name cannot be checked from here');
  });

  it('offers the files ComfyUI does have rather than asking for a rename', async () => {
    const { client, root, loaded } = setup([template()]);
    await loaded();

    (root.querySelector('[data-testid="workflow-template-verify-photomaker-v2-sdxl"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.verifyWorkflowTemplate).toHaveBeenCalled());

    const picker = root.querySelector(
      '[data-testid="workflow-template-asset-photomaker-v2-sdxl-photomaker_model_name"]',
    ) as HTMLSelectElement;
    expect([...picker.options].map((option) => option.value))
      .toEqual(['', 'photomaker-v1.bin', 'photomaker-v2-fp16.bin']);
    picker.value = 'photomaker-v2-fp16.bin';
    picker.dispatchEvent(new Event('change', { bubbles: true }));

    (root.querySelector('[data-testid="workflow-template-install-photomaker-v2-sdxl"]') as HTMLButtonElement).click();
    // The graph is pointed at the file that is actually installed; a
    // downloaded model keeps whatever name its source gave it.
    await vi.waitFor(() => expect(client.installWorkflowTemplate).toHaveBeenCalledWith(
      'photomaker-v2-sdxl',
      'model-1',
      '',
      '',
      [{ node_id: '2', input_name: 'photomaker_model_name', value: 'photomaker-v2-fp16.bin' }],
    ));
  });

  it('marks a family mismatch instead of hiding the template', async () => {
    const { client, root, loaded } = setup([template({ architecture_matches: false })], 'pony');
    await loaded();

    const warning = root.querySelector('[data-testid="workflow-template-mismatch-photomaker-v2-sdxl"]');
    expect(warning?.textContent).toContain('declared as pony');
    // Still installable: the operator may know something the declaration does
    // not, and the warning says what is unmeasured rather than forbidding it.
    expect((root.querySelector('[data-testid="workflow-template-install-photomaker-v2-sdxl"]') as HTMLButtonElement).disabled).toBe(false);
  });

  it('asks for the model family when none is declared', async () => {
    const { client, root, loaded } = setup([template()], '');
    await loaded();

    expect(root.querySelector('[data-testid="workflow-template-architecture-unknown"]')?.textContent)
      .toContain('RealVis XL has no declared model family');
  });

  it('says a newer version adds a graph rather than rewriting the installed one', async () => {
    const { client, root, loaded } = setup([template({ installed_resource_id: 'workflow-1', installed_version: 1, update_available: true, template_version: 2 })]);
    await loaded();

    expect(root.textContent).toContain('Installed');
    expect(root.querySelector('[data-testid="workflow-template-update-photomaker-v2-sdxl"]')?.textContent)
      .toContain('adds a second workflow rather than rewriting the one you have');
  });

  it('installs against the chosen model and refreshes the catalog', async () => {
    const { client, refreshCatalog, root, loaded } = setup([template()]);
    await loaded();

    (root.querySelector('[data-testid="workflow-template-install-photomaker-v2-sdxl"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.installWorkflowTemplate).toHaveBeenCalledWith('photomaker-v2-sdxl', 'model-1', '', '', []));
    expect(refreshCatalog).toHaveBeenCalled();
  });

  it('offers to add a later pass to a recipe, so nobody edits a definition by hand', async () => {
    const swap = template({ id: 'reactor-face-swap', name: 'ReActor face swap', mechanism: 'identity_pass', required_prompt_token: '' });
    const { client, root, loaded } = setup([swap]);
    await loaded();

    expect(root.textContent).toContain('Replaces the face after generation');
    const preset = root.querySelector('[data-testid="workflow-template-preset"]') as HTMLSelectElement;
    preset.value = 'preset-1';
    preset.dispatchEvent(new Event('change', { bubbles: true }));
    (root.querySelector('[data-testid="workflow-template-install-reactor-face-swap"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(client.installWorkflowTemplate)
      .toHaveBeenCalledWith('reactor-face-swap', 'model-1', '', 'preset-1', []));
  });

  it('does not offer a recipe for a pass that conditions during generation', async () => {
    const { client, root, loaded } = setup([template()]);
    await loaded();

    // It is the first pass, so there is nothing to add it after.
    expect(root.querySelector('[data-testid="workflow-template-preset"]')).toBeNull();
  });

  it('labels a video graph by what it makes, not by an identity mechanism', async () => {
    const { root, loaded } = setup([template({
      id: 'wan22-ti2v-5b', name: 'Wan 2.2 text-to-video (5B)', kind: 'video', mechanism: null, architectures: ['wan'], required_prompt_token: '',
      summary: 'Makes a five-second clip from the prompt alone.',
    })]);
    await loaded();
    expect(root.textContent).toContain('Makes a video clip from the prompt');
    expect(root.textContent).not.toContain('Conditions generation');
    // Nothing to add as a later pass: a clip is made whole.
    expect(root.querySelector('[data-testid="workflow-template-preset"]')).toBeNull();
  });

  it('does not retry a failed load on every render', async () => {
    const appState = createState();
    const client = {
      workflowTemplates: vi.fn().mockRejectedValue(new Error('nope')),
      mediaPresets: vi.fn().mockResolvedValue({ items: [] }),
    } as unknown as ApiClient;
    const root = document.createElement('div');
    let view!: WorkflowTemplateView;
    const render = () => root.replaceChildren(view.node('model-1', 'RealVis XL'));
    view = new WorkflowTemplateView(render, appState, client, vi.fn(), { confirm: async () => true, info: () => undefined } as never);
    render();

    await vi.waitFor(() => expect(appState.settingsError).toBe('nope'));
    render();
    render();
    // Rendering is what starts the load, so a failure that did not claim the
    // model would start another load on every render, forever.
    expect(client.workflowTemplates).toHaveBeenCalledTimes(1);
  });
});
