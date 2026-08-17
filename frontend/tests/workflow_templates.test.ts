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
    mechanism: 'reference_adapter',
    architectures: ['sdxl'],
    required_assets: ['photomaker-v2.bin in the ComfyUI models/photomaker folder'],
    required_prompt_token: 'photomaker',
    installed_resource_id: null,
    installed_version: null,
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
      asset_checks: [{ node_id: '2', node_type: 'PhotoMakerLoaderV2', input_name: 'photomaker_model_name', value: 'photomaker-v2.bin', available: false }],
      warnings: [],
    }),
    installWorkflowTemplate: vi.fn().mockResolvedValue({ id: 'workflow-9' }),
  } as unknown as ApiClient;
  const refreshCatalog = vi.fn().mockResolvedValue(undefined);
  const root = document.createElement('div');
  let view!: WorkflowTemplateView;
  const render = () => root.replaceChildren(view.node('model-1', 'RealVis XL'));
  view = new WorkflowTemplateView(render, appState, client, refreshCatalog);
  render();
  return { appState, client, refreshCatalog, root, render };
}

describe('Workflow templates', () => {
  it('offers a shipped graph with what it needs, without claiming it has been run', async () => {
    const { client, root } = setup([template()]);
    await vi.waitFor(() => expect(client.workflowTemplates).toHaveBeenCalledWith('model-1'));

    expect(root.textContent).toContain('PhotoMaker v2 (SDXL)');
    expect(root.textContent).toContain('Conditions generation on the reference');
    expect(root.textContent).toContain('photomaker-v2.bin');
    // The whole point of the panel: nothing here asks which input receives the
    // prompt, and nothing claims a picture has been made.
    expect(root.textContent).toContain('has been generation-tested on this deployment');
    expect(root.querySelector('[data-testid="workflow-template-photomaker-v2-sdxl"]')).not.toBeNull();
  });

  it('says what a check could not see rather than implying it checked everything', async () => {
    const { client, root } = setup([template()]);
    await vi.waitFor(() => expect(client.workflowTemplates).toHaveBeenCalled());

    (root.querySelector('[data-testid="workflow-template-verify-photomaker-v2-sdxl"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.verifyWorkflowTemplate).toHaveBeenCalledWith('photomaker-v2-sdxl'));

    expect(root.textContent).toContain('Not installed: PhotoMakerEncodeV2');
    expect(root.textContent).toContain('photomaker-v2.bin is not available');
    expect(root.textContent).toContain('picks by device rather than by name cannot be checked from here');
  });

  it('marks a family mismatch instead of hiding the template', async () => {
    const { client, root } = setup([template({ architecture_matches: false })], 'pony');
    await vi.waitFor(() => expect(client.workflowTemplates).toHaveBeenCalled());

    const warning = root.querySelector('[data-testid="workflow-template-mismatch-photomaker-v2-sdxl"]');
    expect(warning?.textContent).toContain('declared as pony');
    // Still installable: the operator may know something the declaration does
    // not, and the warning says what is unmeasured rather than forbidding it.
    expect((root.querySelector('[data-testid="workflow-template-install-photomaker-v2-sdxl"]') as HTMLButtonElement).disabled).toBe(false);
  });

  it('asks for the model family when none is declared', async () => {
    const { client, root } = setup([template()], '');
    await vi.waitFor(() => expect(client.workflowTemplates).toHaveBeenCalled());

    expect(root.querySelector('[data-testid="workflow-template-architecture-unknown"]')?.textContent)
      .toContain('RealVis XL has no declared model family');
  });

  it('says a newer version adds a graph rather than rewriting the installed one', async () => {
    const { client, root } = setup([template({ installed_resource_id: 'workflow-1', installed_version: 1, update_available: true, template_version: 2 })]);
    await vi.waitFor(() => expect(client.workflowTemplates).toHaveBeenCalled());

    expect(root.textContent).toContain('Installed');
    expect(root.querySelector('[data-testid="workflow-template-update-photomaker-v2-sdxl"]')?.textContent)
      .toContain('adds a second workflow rather than rewriting the one you have');
  });

  it('installs against the chosen model and refreshes the catalog', async () => {
    const { client, refreshCatalog, root } = setup([template()]);
    await vi.waitFor(() => expect(client.workflowTemplates).toHaveBeenCalled());

    (root.querySelector('[data-testid="workflow-template-install-photomaker-v2-sdxl"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(client.installWorkflowTemplate).toHaveBeenCalledWith('photomaker-v2-sdxl', 'model-1'));
    expect(refreshCatalog).toHaveBeenCalled();
  });

  it('does not retry a failed load on every render', async () => {
    const appState = createState();
    const client = { workflowTemplates: vi.fn().mockRejectedValue(new Error('nope')) } as unknown as ApiClient;
    const root = document.createElement('div');
    let view!: WorkflowTemplateView;
    const render = () => root.replaceChildren(view.node('model-1', 'RealVis XL'));
    view = new WorkflowTemplateView(render, appState, client, vi.fn());
    render();

    await vi.waitFor(() => expect(appState.settingsError).toBe('nope'));
    render();
    render();
    // Rendering is what starts the load, so a failure that did not claim the
    // model would start another load on every render, forever.
    expect(client.workflowTemplates).toHaveBeenCalledTimes(1);
  });
});
