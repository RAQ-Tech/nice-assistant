import { el } from './dom';
import type { MediaCatalogResource } from './types';
import type { WorkflowTemplateView } from './workflow_template_view';

/**
 * The shipped video graph, offered ahead of the paste box.
 *
 * For pictures, bringing your own workflow is one way in among several. For
 * video it was the only way in, and a text-to-video export is a bigger ask
 * than most people should have to meet. So when a workflow is to make video
 * clips, the known-good graph comes first, paired with the video model it
 * should run on, and pasting your own is the fallback beneath it.
 */
export class VideoTemplateOffer {
  private modelId = '';

  constructor(
    private readonly templates: WorkflowTemplateView,
    private readonly renderApp: () => void,
  ) {}

  nodes(videoModels: MediaCatalogResource[]): HTMLElement[] {
    if (!videoModels.length) return [];
    if (!videoModels.some((model) => model.id === this.modelId)) this.modelId = videoModels[0]?.id ?? '';
    const chosen = videoModels.find((model) => model.id === this.modelId) ?? videoModels[0]!;
    return [
      videoModels.length > 1
        ? el('div', { class: 'setting-row' }, [
            el('label', { textContent: 'Video model' }),
            el('select', {
              class: 'chip-select',
              'data-testid': 'workflow-import-video-model',
              onchange: (event: Event) => {
                this.modelId = (event.currentTarget as HTMLSelectElement).value;
                this.renderApp();
              },
            }, videoModels.map((model) => el('option', { value: model.id, selected: model.id === chosen.id, textContent: model.name }))),
          ])
        : null,
      this.templates.node(chosen.id, chosen.name, 'video'),
    ].filter((node): node is HTMLElement => node !== null);
  }
}
