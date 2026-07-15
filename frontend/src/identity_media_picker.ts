import type { ApiClient } from './api';
import { el, errorMessage, formatDate } from './dom';
import type { MediaLibraryItem } from './types';

export type IdentityMediaPickerMode = 'reference' | 'validation';

interface PickerOptions {
  mode: IdentityMediaPickerMode;
  actionLabel: string;
  blockedMessage?: string | undefined;
  actionDisabled: boolean;
  onUse: (item: MediaLibraryItem) => void | Promise<void>;
}

export class IdentityMediaPicker {
  private items: MediaLibraryItem[] = [];
  private mode: IdentityMediaPickerMode | null = null;
  private loading = false;
  private error = '';

  constructor(private readonly renderApp: () => void, private readonly client: ApiClient) {}

  isOpen(mode: IdentityMediaPickerMode): boolean {
    return this.mode === mode;
  }

  close(): void {
    this.mode = null;
  }

  async open(mode: IdentityMediaPickerMode): Promise<void> {
    this.mode = mode;
    this.renderApp();
    if (!this.items.length) await this.load();
  }

  node(options: PickerOptions): HTMLElement {
    return el('div', { class: 'identity-media-picker', 'data-testid': `identity-media-picker-${options.mode}` }, [
      el('div', { class: 'task-model-head' }, [
        el('strong', { textContent: options.mode === 'reference' ? 'Recent generated images' : 'Choose an image' }),
        el('button', { class: 'icon-btn', textContent: '✕ Close', onclick: () => { this.close(); this.renderApp(); } }),
      ]),
      options.blockedMessage ? el('div', { class: 'provider-check-message', textContent: options.blockedMessage }) : null,
      this.loading ? el('div', { class: 'meta', textContent: 'Loading images…' }) : null,
      this.error ? el('div', { class: 'provider-check-message', textContent: this.error }) : null,
      !this.loading && !this.error && !this.items.length
        ? el('div', { class: 'settings-empty-state', textContent: 'No generated images are available yet.' })
        : null,
      el('div', { class: 'identity-media-grid' }, this.items.map((item) =>
        el('div', { class: 'identity-media-option' }, [
          el('img', { src: item.content_url, alt: 'Generated image available for selection', loading: 'lazy' }),
          el('div', { class: 'meta', textContent: formatDate(item.created_at) }),
          el('button', {
            class: 'pill-btn',
            textContent: options.actionLabel,
            disabled: options.actionDisabled,
            onclick: () => void options.onUse(item),
          }),
        ]),
      )),
      el('button', {
        class: 'pill-btn',
        textContent: 'Refresh images',
        disabled: this.loading,
        onclick: () => void this.load(true),
      }),
    ]);
  }

  private async load(force = false): Promise<void> {
    if (this.loading || (this.items.length && !force)) return;
    this.loading = true;
    this.error = '';
    this.renderApp();
    try {
      this.items = (await this.client.mediaLibrary('image', 100)).items;
    } catch (error) {
      this.error = errorMessage(error, 'Generated images could not be loaded.');
    } finally {
      this.loading = false;
      this.renderApp();
    }
  }
}
