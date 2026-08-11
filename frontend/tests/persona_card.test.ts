import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { personaCardBudget, personaCardTokens, renderPersonaCard } from '../src/persona_card';
import { normalizeSettings } from '../src/settings';
import { SettingsView, type Dialogs } from '../src/settings_view';
import { createState } from '../src/state';
import type { Persona } from '../src/types';

// Priced by tests/test_persona_card.py as well. A change to either side's labels or
// estimator breaks one of the two assertions rather than silently showing the operator
// a number the server will not honour.
const SHARED_CARD_FIXTURE = {
  card_definition: 'Runs a neighbourhood bakery and lives above it.',
  card_personality: 'Warm, stubborn, quietly afraid of being left behind.',
  card_style: 'Short sentences. Trails off mid-thought when tired.',
  card_behavior: 'Asks a follow-up before giving advice.',
};
const SHARED_CARD_TOKENS = 131;

function persona(overrides: Partial<Persona> = {}): Persona {
  return {
    id: 'guide',
    workspace_id: 'home',
    workspace_ids: ['home'],
    name: 'Guide',
    avatar_url: null,
    allow_image_sends: true,
    system_prompt: '',
    personality_details: '',
    card_definition: null,
    card_personality: null,
    card_style: null,
    card_behavior: null,
    card_token_estimate: 0,
    card_cap_tokens: 998,
    card_prompt_budget_tokens: 3328,
    card_context_window_tokens: 4096,
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
    ...overrides,
  };
}

function configuredState(current: Persona) {
  const appState = createState();
  appState.settings = normalizeSettings({
    global_default_model: null,
    default_memory_mode: 'saved',
    stt_provider: 'disabled',
    tts_provider: 'disabled',
    tts_format: 'wav',
    openai_api_key: null,
    onboarding_done: true,
    preferences: {},
  });
  appState.settingsSection = 'Personas';
  appState.personas = [current];
  appState.workspaces = [{ id: 'home', name: 'Home', created_at: 1 }];
  return appState;
}

function view(appState: ReturnType<typeof configuredState>, client: Partial<ApiClient>, render = vi.fn()) {
  return new SettingsView(
    render,
    vi.fn(),
    { prompt: vi.fn(), confirm: vi.fn(), info: vi.fn() } as unknown as Dialogs,
    appState,
    client as ApiClient,
  );
}

describe('character card accounting', () => {
  it('prices the shared fixture the same way the platform does', () => {
    expect(personaCardTokens(SHARED_CARD_FIXTURE)).toBe(SHARED_CARD_TOKENS);
  });

  it('renders only populated fields and costs nothing when empty', () => {
    expect(renderPersonaCard({ card_definition: 'Bakes bread.', card_style: '' })).toBe(
      'Character definition (facts about who this persona is): Bakes bread.',
    );
    expect(personaCardTokens({})).toBe(0);
  });

  it('reports the overage once the card passes the cap', () => {
    const budget = personaCardBudget(persona({ card_definition: 'x'.repeat(4000) }));
    expect(budget.over).toBe(true);
    expect(budget.overBy).toBe(budget.used - 998);
  });

  it('shows no history left once the card would consume the whole budget', () => {
    expect(personaCardBudget(persona({ card_definition: 'x'.repeat(9000) })).remainingForHistory).toBe(0);
  });
});

describe('character card editor', () => {
  it('updates the token count and budget meter while the card is typed', () => {
    const current = persona();
    const appState = configuredState(current);
    const node = view(appState, {}).node();

    const meter = node.querySelector('[data-testid="character-card-meter-guide"]') as HTMLElement;
    expect(meter.textContent).toContain('0 of 998 tokens');
    expect(meter.classList.contains('settings-warning')).toBe(false);

    const definition = node.querySelector('.character-card-field textarea') as HTMLTextAreaElement;
    definition.value = SHARED_CARD_FIXTURE.card_definition;
    definition.dispatchEvent(new Event('input'));

    const count = node.querySelector('[data-testid="character-card-count-card_definition-guide"]') as HTMLElement;
    expect(count.textContent).toBe('35 tokens');
    expect(meter.textContent).toContain('35 of 998 tokens');
    expect(meter.textContent).toContain('tokens left for conversation history');
  });

  it('warns before saving once the card is over the cap', () => {
    const current = persona({ card_definition: 'x'.repeat(4000) });
    const node = view(configuredState(current), {}).node();
    const meter = node.querySelector('[data-testid="character-card-meter-guide"]') as HTMLElement;

    expect(meter.classList.contains('settings-warning')).toBe(true);
    expect(meter.textContent).toContain('over the limit');
  });

  it('saves the card through its own route and keeps the server answer', async () => {
    const current = persona();
    const appState = configuredState(current);
    const updatePersonaCard = vi.fn().mockResolvedValue(
      persona({ ...SHARED_CARD_FIXTURE, card_token_estimate: SHARED_CARD_TOKENS }),
    );
    const node = view(appState, { updatePersonaCard }).node();

    current.card_style = SHARED_CARD_FIXTURE.card_style;
    (node.querySelector('[data-testid="character-card-save-guide"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(updatePersonaCard).toHaveBeenCalled());
    expect(updatePersonaCard.mock.calls[0]?.[1]).toMatchObject({ card_style: SHARED_CARD_FIXTURE.card_style });
    await vi.waitFor(() => expect(appState.personas[0]?.card_token_estimate).toBe(SHARED_CARD_TOKENS));
  });

  it('surfaces the rejection message when the card does not fit', async () => {
    const appState = configuredState(persona());
    const updatePersonaCard = vi
      .fn()
      .mockRejectedValue(new Error('This character card is 1200 tokens and the limit is 998.'));
    const node = view(appState, { updatePersonaCard }).node();

    (node.querySelector('[data-testid="character-card-save-guide"]') as HTMLButtonElement).click();

    await vi.waitFor(() => expect(appState.settingsError).toContain('the limit is 998'));
  });
});
