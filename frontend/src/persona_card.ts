import type { Persona } from './types';

/**
 * Mirrors `app/persona_card.py` so the editor can price a card while it is being typed.
 * The server estimate returned on save stays authoritative; `tests/test_persona_card.py`
 * and `frontend/tests/persona_card.test.ts` price the same card so drift fails a test.
 */

export const PERSONA_CARD_FIELDS = [
  'card_definition',
  'card_personality',
  'card_style',
  'card_behavior',
] as const;

export type PersonaCardField = (typeof PERSONA_CARD_FIELDS)[number];

/** Sent together on the card route; only the four above are capped. */
export const PERSONA_CARD_STORED_FIELDS = [...PERSONA_CARD_FIELDS, 'card_example_dialogue'] as const;

export const PERSONA_CARD_LABELS: Record<PersonaCardField, string> = {
  card_definition: 'Character definition (facts about who this persona is)',
  card_personality: 'Character personality (disposition, values, flaws, fears)',
  card_style: 'Character style (how this persona speaks)',
  card_behavior: 'Character behavior (how this persona acts)',
};

export const PERSONA_CARD_EDITOR_LABELS: Record<PersonaCardField, string> = {
  card_definition: 'Definition',
  card_personality: 'Personality',
  card_style: 'Style',
  card_behavior: 'Behavior',
};

export const PERSONA_CARD_HELP: Record<PersonaCardField, string> = {
  card_definition: 'Facts about this persona: age, work, living situation, history.',
  card_personality: 'Disposition, values, flaws, and fears.',
  card_style: 'Speech patterns, vocabulary, rhythm, and verbal tics.',
  card_behavior: 'How this persona acts: initiative, humor, conflict, affection.',
};

export const EXAMPLE_BLOCK_DELIMITER = '<START>';
export const EXAMPLE_USER_PLACEHOLDER = '{{user}}';
export const EXAMPLE_CHAR_PLACEHOLDER = '{{char}}';
export const EXAMPLE_USER_NAME = 'User';

export type PersonaCardValues = Partial<Record<PersonaCardField, string | null>> & {
  card_example_dialogue?: string | null;
};

export function exampleDialogueBlocks(raw: string | null | undefined): string[] {
  const blocks: string[] = [];
  let current: string[] = [];
  for (const line of String(raw ?? '').split('\n')) {
    if (line.trim() === EXAMPLE_BLOCK_DELIMITER) {
      if (current.some((item) => item.trim())) blocks.push(current.join('\n').trim());
      current = [];
      continue;
    }
    current.push(line);
  }
  if (current.some((item) => item.trim())) blocks.push(current.join('\n').trim());
  return blocks;
}

export function renderExampleBlock(block: string, personaName: string): string {
  return block
    .split(EXAMPLE_CHAR_PLACEHOLDER)
    .join(personaName || 'Assistant')
    .split(EXAMPLE_USER_PLACEHOLDER)
    .join(EXAMPLE_USER_NAME);
}

/** Whole exchanges up to the budget; later ones are dropped first. Mirrors persona_card.py. */
export function selectedExampleBlocks(
  raw: string | null | undefined,
  personaName: string,
  budgetTokens: number,
): string[] {
  const selected: string[] = [];
  for (const block of exampleDialogueBlocks(raw)) {
    const candidate = [...selected, renderExampleBlock(block, personaName)];
    if (estimateTokens(candidate.join('\n\n')) > budgetTokens) break;
    selected.length = 0;
    selected.push(...candidate);
  }
  return selected;
}

export function personaCardValues(source: PersonaCardValues): Record<PersonaCardField, string> {
  return PERSONA_CARD_FIELDS.reduce(
    (values, field) => {
      values[field] = (source[field] ?? '').trim();
      return values;
    },
    {} as Record<PersonaCardField, string>,
  );
}

export function renderPersonaCard(source: PersonaCardValues): string {
  const values = personaCardValues(source);
  return PERSONA_CARD_FIELDS.filter((field) => values[field])
    .map((field) => `${PERSONA_CARD_LABELS[field]}: ${values[field]}`)
    .join('\n');
}

/** The conservative bytes-per-three estimate the platform uses before a provider reports usage. */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  return Math.max(1, Math.ceil(new TextEncoder().encode(text).length / 3));
}

export function personaCardTokens(source: PersonaCardValues): number {
  return estimateTokens(renderPersonaCard(source));
}

export function personaCardFieldTokens(source: PersonaCardValues, field: PersonaCardField): number {
  const value = (source[field] ?? '').trim();
  return value ? estimateTokens(`${PERSONA_CARD_LABELS[field]}: ${value}`) : 0;
}

export interface PersonaCardBudget {
  used: number;
  cap: number;
  promptBudget: number;
  contextWindow: number;
  remainingForHistory: number;
  overBy: number;
  over: boolean;
}

export function personaCardBudget(persona: Persona): PersonaCardBudget {
  const cap = persona.card_cap_tokens ?? 0;
  const promptBudget = persona.card_prompt_budget_tokens ?? 0;
  const used = personaCardTokens(persona);
  return {
    used,
    cap,
    promptBudget,
    contextWindow: persona.card_context_window_tokens ?? 0,
    // Memory (15%) and summary (20%) are reserved before history; the card competes with what is left.
    remainingForHistory: Math.max(0, Math.round(promptBudget * 0.65) - used),
    overBy: Math.max(0, used - cap),
    over: used > cap,
  };
}
