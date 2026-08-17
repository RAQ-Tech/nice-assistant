import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { PersonaLoreCopyView } from '../src/persona_lore_copy_view';
import { createState } from '../src/state';
import type { Persona, PersonaLoreCopyGroup, PersonaLoreEntry } from '../src/types';

function persona(): Persona {
  return {
    id: 'guide',
    workspace_id: 'home',
    workspace_ids: ['home'],
    name: 'Ada',
    avatar_url: null,
    system_prompt: '',
    personality_details: '',
    traits: {},
    default_model: null,
    voice_preferences: {},
    created_at: 1,
  };
}

function group(): PersonaLoreCopyGroup {
  return {
    persona_id: 'bo',
    persona_name: 'Bo',
    entries: [{ id: 'lighthouse', title: 'The lighthouse', always_on: false, token_estimate: 14 }],
  };
}

function copied(): PersonaLoreEntry {
  return {
    id: 'copy-1',
    persona_id: 'guide',
    title: 'The lighthouse',
    keys: ['lighthouse'],
    secondary_keys: [],
    content: 'Dark since 1974.',
    always_on: false,
    case_sensitive: false,
    match_word_forms: true,
    priority: 70,
    enabled: true,
    token_estimate: 14,
    budget_tokens: 399,
    created_at: 1,
    updated_at: 1,
  };
}

async function opened(client: Partial<ApiClient>) {
  const appState = createState();
  const render = vi.fn();
  const adopted: PersonaLoreEntry[] = [];
  const view = new PersonaLoreCopyView(
    render,
    (_persona, entry) => adopted.push(entry),
    appState,
    client as ApiClient,
  );
  const current = persona();
  const details = view.node(current) as HTMLDetailsElement;
  details.open = true;
  details.dispatchEvent(new Event('toggle'));
  await vi.waitFor(() => expect(render).toHaveBeenCalled());
  return { appState, view, adopted, current, node: view.node(current) };
}

describe('taking a lore entry from another persona', () => {
  it('does not ask who is nearby until the section is opened', () => {
    const copyablePersonaLore = vi.fn().mockResolvedValue({ groups: [group()] });
    const view = new PersonaLoreCopyView(vi.fn(), vi.fn(), createState(), {
      copyablePersonaLore,
    } as unknown as ApiClient);

    view.node(persona());

    expect(copyablePersonaLore).not.toHaveBeenCalled();
  });

  it('offers each nearby entry by name', async () => {
    const { node } = await opened({
      copyablePersonaLore: vi.fn().mockResolvedValue({ groups: [group()] }),
    });

    expect(node.textContent).toContain('Bo');
    expect(node.querySelector('[data-testid="lore-copy-lighthouse"]')?.textContent)
      .toBe('Copy "The lighthouse"');
  });

  it('says the copy will not follow the original', async () => {
    const { node } = await opened({
      copyablePersonaLore: vi.fn().mockResolvedValue({ groups: [group()] }),
    });

    // Somebody who thinks these stay linked will edit one and expect both to
    // change, and only find out otherwise when a persona says something stale.
    expect(node.querySelector('[data-testid="lore-copy-warning-guide"]')?.textContent)
      .toContain('Editing the original later does not change it');
  });

  it('hands the copy to the lorebook and stops offering it', async () => {
    let taken = false;
    const copyablePersonaLore = vi.fn(async () => ({ groups: taken ? [] : [group()] }));
    const copyPersonaLore = vi.fn(async () => {
      taken = true;
      return copied();
    });
    const built = await opened({ copyablePersonaLore, copyPersonaLore });

    (built.node.querySelector('[data-testid="lore-copy-lighthouse"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(copyPersonaLore).toHaveBeenCalledWith('guide', 'lighthouse'));
    expect(built.adopted.map((entry) => entry.id)).toEqual(['copy-1']);
    // Offering it again is how a lore list fills with duplicates nobody meant.
    await vi.waitFor(() =>
      expect(built.view.node(built.current).querySelector('[data-testid="lore-copy-lighthouse"]')).toBeNull(),
    );
  });

  it('shows why a copy was refused rather than losing it', async () => {
    const refusal = 'Lore can only be copied between personas in the same workspace.';
    const built = await opened({
      copyablePersonaLore: vi.fn().mockResolvedValue({ groups: [group()] }),
      copyPersonaLore: vi.fn().mockRejectedValue(new Error(refusal)),
    });

    (built.node.querySelector('[data-testid="lore-copy-lighthouse"]') as HTMLButtonElement).click();

    // The server explains itself; repeating a vaguer sentence over the top
    // would tell somebody less than the thing they were already going to read.
    await vi.waitFor(() => expect(built.appState.settingsError).toBe(refusal));
  });
});
