import type { ApiClient } from './api';
import type { ChatController } from './chat';
import { el, errorMessage, formatDate } from './dom';
import type { Dialogs } from './settings_view';
import type { AppState, Chat } from './types';

interface ChatDrawerCallbacks {
  render: () => void;
  openChat: (chatId: string) => void;
  openNewChat: () => void;
  goHome: () => void;
}

export class ChatDrawer {
  private readonly selectedIds = new Set<string>();
  private selectionMode = false;

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly chat: ChatController,
    private readonly dialogs: Dialogs,
    private readonly callbacks: ChatDrawerCallbacks,
  ) {}

  node(): HTMLElement {
    const visibleChats = this.appState.chats.filter((item) =>
      (item.title ?? '').toLowerCase().includes(this.appState.chatSearch.toLowerCase()),
    );
    const rows = visibleChats.map((item) => this.row(item));
    return el('aside', { class: `drawer glass ${this.appState.drawerOpen ? 'open' : ''}` }, [
      el('div', { class: 'drawer-head' }, [
        el('strong', { textContent: 'Chats' }),
        el('div', { class: 'chips' }, [
          el('button', {
            class: 'icon-btn',
            textContent: this.selectionMode ? 'Done' : 'Manage',
            disabled: this.appState.chats.length === 0,
            'data-testid': 'manage-chats',
            onclick: () => {
              this.selectionMode = !this.selectionMode;
              if (!this.selectionMode) this.selectedIds.clear();
              this.callbacks.render();
            },
          }),
          el('button', {
            class: 'icon-btn',
            textContent: '✕',
            onclick: () => { this.appState.drawerOpen = false; this.callbacks.render(); },
          }),
        ]),
      ]),
      el('button', {
        class: 'send-btn',
        textContent: '+ New Chat',
        'data-testid': 'new-chat',
        onclick: this.callbacks.openNewChat,
      }),
      el('input', {
        class: 'search-input',
        placeholder: 'Search chats…',
        value: this.appState.chatSearch,
        oninput: (event: Event) => {
          this.appState.chatSearch = (event.currentTarget as HTMLInputElement).value;
          this.callbacks.render();
        },
      }),
      this.selectionMode ? this.bulkBar(visibleChats) : null,
      el('div', { class: 'drawer-list' }, rows.length ? rows : el('div', { class: 'meta', textContent: 'No chats yet.' })),
    ]);
  }

  reset(): void {
    this.selectedIds.clear();
    this.selectionMode = false;
  }

  private row(item: Chat): HTMLElement {
    return el('div', {
      class: `chat-row ${item.id === this.appState.currentChat?.id ? 'active' : ''} ${this.selectionMode ? 'selecting' : ''}`,
      onclick: () => {
        if (this.selectionMode) {
          this.toggle(item.id);
          return;
        }
        this.appState.drawerOpen = false;
        this.callbacks.openChat(item.id);
      },
    }, [
      this.selectionMode ? el('input', {
        type: 'checkbox',
        checked: this.selectedIds.has(item.id),
        ariaLabel: `Select ${item.title || 'chat'}`,
        onclick: (event: Event) => event.stopPropagation(),
        onchange: (event: Event) => {
          if ((event.currentTarget as HTMLInputElement).checked) this.selectedIds.add(item.id);
          else this.selectedIds.delete(item.id);
          this.callbacks.render();
        },
      }) : null,
      el('div', { class: 'title', textContent: item.title || 'Untitled chat' }),
      el('div', { class: 'meta', textContent: formatDate(item.updated_at || item.created_at) }),
      !this.selectionMode ? el('div', { class: 'chat-actions' }, [
        el('button', {
          class: 'icon-btn',
          textContent: '✎',
          title: 'Rename chat',
          onclick: (event: Event) => { event.stopPropagation(); void this.rename(item); },
        }),
        el('button', {
          class: 'icon-btn',
          textContent: 'Hide',
          title: 'Hide chat',
          onclick: (event: Event) => { event.stopPropagation(); void this.hide(item); },
        }),
      ]) : null,
    ]);
  }

  private bulkBar(visibleChats: Chat[]): HTMLElement {
    return el('div', { class: 'chat-bulk-bar', 'data-testid': 'chat-bulk-actions' }, [
      el('div', { class: 'meta', textContent: `${this.selectedIds.size} selected` }),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'pill-btn',
          textContent: 'Select visible',
          disabled: visibleChats.length === 0,
          onclick: () => { visibleChats.forEach((item) => this.selectedIds.add(item.id)); this.callbacks.render(); },
        }),
        el('button', {
          class: 'pill-btn',
          textContent: 'Clear',
          disabled: this.selectedIds.size === 0,
          onclick: () => { this.selectedIds.clear(); this.callbacks.render(); },
        }),
        el('button', {
          class: 'pill-btn',
          textContent: 'Hide',
          disabled: this.selectedIds.size === 0,
          onclick: () => void this.bulkAction('hide'),
        }),
        el('button', {
          class: 'pill-btn danger',
          textContent: 'Delete',
          disabled: this.selectedIds.size === 0,
          onclick: () => void this.bulkAction('delete'),
        }),
      ]),
    ]);
  }

  private toggle(id: string): void {
    if (this.selectedIds.has(id)) this.selectedIds.delete(id);
    else this.selectedIds.add(id);
    this.callbacks.render();
  }

  private async rename(item: Chat): Promise<void> {
    const title = await this.dialogs.prompt('Rename chat', 'Choose a new title.', item.title ?? '');
    if (title?.trim()) await this.chat.rename(item, title);
  }

  private async hide(item: Chat): Promise<void> {
    if (!(await this.dialogs.confirm('Hide chat', `Hide ${item.title || 'this chat'}?`, 'Hide'))) return;
    await this.chat.hide(item);
    this.callbacks.goHome();
  }

  private async bulkAction(action: 'hide' | 'delete'): Promise<void> {
    const ids = [...this.selectedIds];
    if (!ids.length) return;
    const confirmed = action === 'delete'
      ? await this.dialogs.confirm(
          'Permanently delete chats',
          `Permanently delete ${ids.length} selected ${ids.length === 1 ? 'chat' : 'chats'} and their transcripts? This cannot be undone. Generated media and completed job records are retained.`,
          'Delete permanently',
        )
      : await this.dialogs.confirm(
          'Hide chats',
          `Hide ${ids.length} selected ${ids.length === 1 ? 'chat' : 'chats'} from the chat list?`,
          'Hide',
        );
    if (!confirmed) return;
    try {
      await this.client.bulkChatAction(action, ids);
      const removed = new Set(ids);
      this.appState.chats = this.appState.chats.filter((item) => !removed.has(item.id));
      this.reset();
      if (this.appState.currentChat && removed.has(this.appState.currentChat.id)) {
        this.appState.currentChat = null;
        this.appState.messages = [];
        this.appState.capabilityRequests = [];
        this.callbacks.goHome();
      }
    } catch (error) {
      this.appState.uiError = errorMessage(error, `Unable to ${action} the selected chats.`);
    }
    this.callbacks.render();
  }
}
