import type { ChatCreateSelection } from './chat';
import { el } from './dom';
import type { AppState } from './types';

export function newChatModal(
  state: AppState,
  onChange: () => void,
  onCreate: (selection: ChatCreateSelection) => Promise<unknown>,
): HTMLElement {
  const persona = state.personas.find((item) => item.id === state.newChatPersonaId);
  const workspaceIds = new Set(persona?.workspace_ids ?? []);
  return el('div', { class: 'modal-backdrop' }, [
    el('div', { class: 'modal-card glass', role: 'dialog', 'aria-modal': 'true' }, [
      el('h3', { textContent: 'Start a new chat' }),
      el('label', { textContent: 'Persona' }),
      el(
        'select',
        {
          class: 'chip-select',
          value: state.newChatPersonaId ?? '',
          'data-testid': 'new-chat-persona',
          onchange: (event: Event) => {
            state.newChatPersonaId = (event.currentTarget as HTMLSelectElement).value || null;
            state.newChatContextKey = '';
            onChange();
          },
        },
        state.personas.map((item) =>
          el('option', {
            value: item.id,
            selected: item.id === state.newChatPersonaId,
            textContent: item.name,
          }),
        ),
      ),
      el('label', { textContent: 'Access context' }),
      el(
        'select',
        {
          class: 'chip-select',
          value: state.newChatContextKey,
          'data-testid': 'new-chat-context',
          onchange: (event: Event) => {
            state.newChatContextKey = (event.currentTarget as HTMLSelectElement).value;
            onChange();
          },
        },
        [
          el('option', {
            value: '',
            selected: !state.newChatContextKey,
            textContent: 'Choose a context…',
          }),
          el('option', {
            value: 'personal',
            selected: state.newChatContextKey === 'personal',
            textContent: 'Personal',
          }),
          ...state.workspaces
            .filter((workspace) => workspaceIds.has(workspace.id))
            .map((workspace) =>
              el('option', {
                value: `workspace:${workspace.id}`,
                selected: state.newChatContextKey === `workspace:${workspace.id}`,
                textContent: workspace.name,
              }),
            ),
        ],
      ),
      el('div', { class: 'modal-actions' }, [
        el('button', {
          class: 'pill-btn',
          textContent: 'Cancel',
          onclick: () => {
            state.showNewChatPersonaModal = false;
            onChange();
          },
        }),
        el('button', {
          class: 'send-btn',
          textContent: 'Create chat',
          disabled: !state.newChatPersonaId || !state.newChatContextKey,
          'data-testid': 'new-chat-confirm',
          onclick: () => {
            const selection = createSelection(state);
            if (selection) {
              state.showNewChatPersonaModal = false;
              onChange();
              void onCreate(selection).finally(onChange);
            }
          },
        }),
      ]),
    ]),
  ]);
}

function createSelection(state: AppState): ChatCreateSelection | null {
  const personaId = state.newChatPersonaId;
  if (!personaId || !state.newChatContextKey) return null;
  const context = state.newChatContextKey.startsWith('workspace:')
    ? {
        kind: 'workspace' as const,
        workspaceId: state.newChatContextKey.slice('workspace:'.length),
      }
    : { kind: 'personal' as const };
  return { personaId, context };
}
