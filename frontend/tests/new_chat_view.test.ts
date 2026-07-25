import { describe, expect, it, vi } from 'vitest';

import { newChatModal } from '../src/new_chat_view';
import { createState } from '../src/state';
import type { Persona } from '../src/types';

function persona(): Persona {
  return {
    id: 'persona-1',
    workspace_id: 'workspace-1',
    workspace_ids: ['workspace-1'],
    name: 'Avery',
    avatar_url: null,
    system_prompt: null,
    personality_details: null,
    traits: {},
    default_model: null,
    preferred_voice: null,
    preferred_tts_model: null,
    preferred_tts_speed: null,
    preferred_voice_openai: null,
    preferred_tts_model_openai: null,
    preferred_tts_speed_openai: null,
    preferred_voice_local: null,
    preferred_tts_model_local: null,
    preferred_tts_speed_local: null,
    created_at: 1,
  };
}

describe('new chat view', () => {
  it('requires and immediately applies an explicit access context', async () => {
    const state = createState();
    state.showNewChatPersonaModal = true;
    state.newChatPersonaId = 'persona-1';
    state.personas = [persona()];
    state.workspaces = [
      { id: 'workspace-1', name: 'Studio', created_at: 1 },
      { id: 'workspace-2', name: 'Private', created_at: 2 },
    ];
    const create = vi.fn().mockResolvedValue(undefined);
    const render = () => {
      document.body.replaceChildren(newChatModal(state, render, create));
    };
    render();

    expect(document.querySelector<HTMLButtonElement>('[data-testid="new-chat-confirm"]')?.disabled).toBe(true);
    const context = document.querySelector<HTMLSelectElement>('[data-testid="new-chat-context"]')!;
    expect([...context.options].map((option) => option.textContent)).toEqual([
      'Choose a context…',
      'Personal',
      'Studio',
    ]);

    context.value = 'personal';
    context.dispatchEvent(new Event('change', { bubbles: true }));
    const confirm = document.querySelector<HTMLButtonElement>('[data-testid="new-chat-confirm"]')!;
    expect(confirm.disabled).toBe(false);
    confirm.click();

    await vi.waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        personaId: 'persona-1',
        context: { kind: 'personal' },
      }),
    );
    expect(state.showNewChatPersonaModal).toBe(false);
  });
});
