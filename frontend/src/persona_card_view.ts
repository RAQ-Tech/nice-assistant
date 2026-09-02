import { api, type ApiClient } from './api';
import { el, errorMessage } from './dom';
import {
  EXAMPLE_BLOCK_DELIMITER,
  EXAMPLE_CHAR_PLACEHOLDER,
  EXAMPLE_USER_PLACEHOLDER,
  PERSONA_CARD_EDITOR_LABELS,
  PERSONA_CARD_FIELDS,
  PERSONA_CARD_HELP,
  PERSONA_CARD_STORED_FIELDS,
  exampleDialogueBlocks,
  personaCardBudget,
  personaCardFieldTokens,
  selectedExampleBlocks,
  type PersonaCardField,
} from './persona_card';
import { longField } from './settings_page';
import { advancedSettings } from './settings_ui';
import { state } from './state';
import type { AppState, Persona } from './types';

/**
 * The character card editor. It prices the card while it is typed so the operator sees the
 * cost before the save-time cap rejects it, and saves through the card route so the cap
 * keeps a single enforcement point.
 */
function exampleDialogueSummary(persona: Persona): string {
  const authored = exampleDialogueBlocks(persona.card_example_dialogue).length;
  if (!authored) return `No example exchanges yet. Separate each one with a ${EXAMPLE_BLOCK_DELIMITER} line.`;
  const budget = persona.example_budget_tokens ?? 0;
  const included = selectedExampleBlocks(persona.card_example_dialogue, persona.name, budget).length;
  const noun = authored === 1 ? 'exchange' : 'exchanges';
  if (included >= authored) return `${authored} ${noun}, all of which fit in the ${budget}-token allowance.`;
  return (
    `${authored} ${noun}; the first ${included} fit in the ${budget}-token allowance and the rest are left out. `
    + 'Shorten them, or raise the model context allocation in Settings.'
  );
}

export class PersonaCardView {
  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState = state,
    private readonly client: ApiClient = api,
  ) {}

  node(persona: Persona): HTMLElement {
    const meter = el('div', {
      class: 'meta character-card-meter',
      'data-testid': `character-card-meter-${persona.id}`,
    });
    const exampleMeter = el('div', {
      class: 'meta character-card-meter',
      'data-testid': `character-card-example-meter-${persona.id}`,
    });
    const counts = new Map<PersonaCardField, HTMLElement>();
    const refresh = (): void => {
      const budget = personaCardBudget(persona);
      for (const [field, node] of counts) {
        node.textContent = `${personaCardFieldTokens(persona, field)} tokens`;
      }
      meter.textContent = budget.over
        ? `${budget.used} of ${budget.cap} tokens — ${budget.overBy} over the limit. Saving this card will be rejected.`
        : `${budget.used} of ${budget.cap} tokens · about ${budget.remainingForHistory} tokens left for conversation history.`;
      meter.classList.toggle('settings-warning', budget.over);
      exampleMeter.textContent = exampleDialogueSummary(persona);
    };
    const fields = PERSONA_CARD_FIELDS.map((field) => {
      const count = el('span', { class: 'meta', 'data-testid': `character-card-count-${field}-${persona.id}` });
      counts.set(field, count);
      return el('div', { class: 'character-card-field' }, [
        longField(
          PERSONA_CARD_EDITOR_LABELS[field],
          persona[field] ?? '',
          (value) => {
            persona[field] = value;
            refresh();
          },
          { hover: PERSONA_CARD_HELP[field] },
        ),
        count,
      ]);
    });
    const examples = el('div', { class: 'character-card-field' }, [
      longField(
        'Example dialogue',
        persona.card_example_dialogue ?? '',
        (value) => {
          persona.card_example_dialogue = value;
          refresh();
        },
        { hover: `Sample exchanges that show how this persona talks. Separate each one with a ${EXAMPLE_BLOCK_DELIMITER} `
          + `line, and write ${EXAMPLE_CHAR_PLACEHOLDER} for the persona and ${EXAMPLE_USER_PLACEHOLDER} for whoever `
          + 'is talking to them.' },
      ),
    ]);
    refresh();
    return advancedSettings(
      'Character card',
      'Durable character material sent with every turn. The card is never dropped to make room, so it is capped when '
        + 'you save it. Example dialogue is optional context: it yields before the conversation does.',
      [
        ...fields,
        meter,
        examples,
        exampleMeter,
        el('div', { class: 'chips' }, [
          el('button', {
            class: 'pill-btn',
            textContent: 'Save character card',
            'data-testid': `character-card-save-${persona.id}`,
            onclick: () => void this.save(persona),
          }),
        ]),
      ],
      { testId: `character-card-${persona.id}` },
    );
  }

  private async save(persona: Persona): Promise<void> {
    const card = PERSONA_CARD_STORED_FIELDS.reduce<Record<string, string>>((values, field) => {
      values[field] = persona[field] ?? '';
      return values;
    }, {});
    try {
      const updated = await this.client.updatePersonaCard(persona.id, card);
      this.appState.personas = this.appState.personas.map((item) => (item.id === updated.id ? updated : item));
      this.appState.settingsError = '';
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'The character card could not be saved.');
    }
    this.renderApp();
  }
}
