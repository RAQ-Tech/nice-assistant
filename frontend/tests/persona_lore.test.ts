import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { PersonaLoreView } from '../src/persona_lore_view';
import { createState } from '../src/state';
import type { Persona, PersonaLoreEntry } from '../src/types';

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
    default_model: null, voice_preferences: {},
    created_at: 1,
  };
}

function loreEntry(overrides: Partial<PersonaLoreEntry> = {}): PersonaLoreEntry {
  return {
    id: 'sister',
    persona_id: 'guide',
    title: 'Sister',
    keys: ['sister', 'Nell'],
    secondary_keys: [],
    content: 'Her sister Nell is a nurse.',
    always_on: false,
    case_sensitive: false,
    match_word_forms: true,
    priority: 50,
    enabled: true,
    token_estimate: 12,
    budget_tokens: 399,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  };
}

function build(client: Partial<ApiClient>, render = vi.fn()) {
  const appState = createState();
  const view = new PersonaLoreView(render, appState, client as ApiClient);
  return { appState, view, render };
}

async function opened(client: Partial<ApiClient>) {
  const built = build(client);
  const current = persona();
  built.view.node(current);
  const details = built.view.node(current) as HTMLDetailsElement;
  details.open = true;
  details.dispatchEvent(new Event('toggle'));
  await vi.waitFor(() => expect(built.render).toHaveBeenCalled());
  return { ...built, node: built.view.node(current), current };
}

describe('lorebook editor', () => {
  it('loads entries only when the section is opened', async () => {
    const personaLore = vi.fn().mockResolvedValue({ items: [loreEntry()] });
    const built = build({ personaLore });
    const current = persona();

    built.view.node(current);
    expect(personaLore).not.toHaveBeenCalled();

    const details = built.view.node(current) as HTMLDetailsElement;
    details.open = true;
    details.dispatchEvent(new Event('toggle'));

    await vi.waitFor(() => expect(personaLore).toHaveBeenCalledWith('guide'));
    expect(built.view.node(current).querySelector('[data-testid="lore-entry-sister"]')).toBeTruthy();
  });

  it('summarizes what an entry fires on', async () => {
    const { node } = await opened({ personaLore: vi.fn().mockResolvedValue({ items: [loreEntry()] }) });
    expect(node.querySelector('[data-testid="lore-entry-sister"]')?.textContent).toContain('Fires on: sister, Nell');
  });

  it('says plainly when an entry is always included', async () => {
    const entry = loreEntry({ always_on: true, keys: [] });
    const { node } = await opened({ personaLore: vi.fn().mockResolvedValue({ items: [entry] }) });
    expect(node.querySelector('[data-testid="lore-entry-sister"]')?.textContent).toContain('Always included');
  });

  it('marks a disabled entry in its own title', async () => {
    const entry = loreEntry({ enabled: false });
    const { node } = await opened({ personaLore: vi.fn().mockResolvedValue({ items: [entry] }) });
    expect(node.querySelector('[data-testid="lore-entry-sister"]')?.textContent).toContain('Sister (off)');
  });

  it('sends edited keywords as a list', async () => {
    const updatePersonaLore = vi.fn().mockResolvedValue(loreEntry({ keys: ['bakery'] }));
    const { node } = await opened({
      personaLore: vi.fn().mockResolvedValue({ items: [loreEntry()] }),
      updatePersonaLore,
    });

    const keywords = [...node.querySelectorAll('input')].find(
      (input) => (input as HTMLInputElement).value === 'sister, Nell',
    ) as HTMLInputElement;
    keywords.value = ' bakery , oven ,, ';
    keywords.dispatchEvent(new Event('input'));
    (node.querySelector('[data-testid="lore-save-sister"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(updatePersonaLore).toHaveBeenCalled());
    expect(updatePersonaLore.mock.calls[0]?.[2]).toMatchObject({ keys: ['bakery', 'oven'] });
  });

  it('reports which entries a pasted message fires and which fit', async () => {
    const previewPersonaLore = vi.fn().mockResolvedValue({
      budget_tokens: 399,
      used_tokens: 15,
      items: [
        { id: 'sister', title: 'Sister', always_on: false, fired_keys: ['sister'], priority: 50, token_estimate: 12, included: true },
        { id: 'bakery', title: 'Bakery', always_on: false, fired_keys: ['bakery'], priority: 10, token_estimate: 900, included: false },
      ],
    });
    const { node, view, current } = await opened({
      personaLore: vi.fn().mockResolvedValue({ items: [loreEntry()] }),
      previewPersonaLore,
    });

    const box = [...node.querySelectorAll('textarea')].at(-1) as HTMLTextAreaElement;
    box.value = 'how is your sister';
    box.dispatchEvent(new Event('input'));
    (node.querySelector('[data-testid="lore-preview-guide"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(previewPersonaLore).toHaveBeenCalledWith('guide', 'how is your sister'));
    const refreshed = view.node(current);
    const result = refreshed.querySelector('[data-testid="lore-preview-result-guide"]') as HTMLElement;
    expect(result.textContent).toContain('2 entries fire; 1 fit');
    expect(refreshed.textContent).toContain('✓ Sister');
    expect(refreshed.textContent).toContain('✕ Bakery');
    expect(refreshed.textContent).toContain('left out, over the allowance');
  });

  it('says so when nothing fires', async () => {
    const previewPersonaLore = vi.fn().mockResolvedValue({ budget_tokens: 399, used_tokens: 0, items: [] });
    const { node, view, current } = await opened({
      personaLore: vi.fn().mockResolvedValue({ items: [loreEntry()] }),
      previewPersonaLore,
    });

    const box = [...node.querySelectorAll('textarea')].at(-1) as HTMLTextAreaElement;
    box.value = 'nothing relevant';
    box.dispatchEvent(new Event('input'));
    (node.querySelector('[data-testid="lore-preview-guide"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(previewPersonaLore).toHaveBeenCalled());
    expect(
      (view.node(current).querySelector('[data-testid="lore-preview-result-guide"]') as HTMLElement).textContent,
    ).toBe('Nothing fires on that message.');
  });

  it('surfaces a rejected entry instead of dropping it silently', async () => {
    const updatePersonaLore = vi.fn().mockRejectedValue(new Error('A lore entry needs at least one keyword'));
    const built = await opened({
      personaLore: vi.fn().mockResolvedValue({ items: [loreEntry()] }),
      updatePersonaLore,
    });

    (built.node.querySelector('[data-testid="lore-save-sister"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(built.appState.settingsError).toContain('needs at least one keyword'));
  });

  it('removes a deleted entry from the list', async () => {
    const deletePersonaLore = vi.fn().mockResolvedValue({ ok: true });
    const { node, view, current } = await opened({
      personaLore: vi.fn().mockResolvedValue({ items: [loreEntry()] }),
      deletePersonaLore,
    });

    (node.querySelector('[data-testid="lore-delete-sister"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(deletePersonaLore).toHaveBeenCalledWith('guide', 'sister'));
    expect(view.node(current).querySelector('[data-testid="lore-entry-sister"]')).toBeNull();
  });
});
