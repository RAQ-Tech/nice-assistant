import { el, formatDate } from './dom';
import {
  boundedNumber,
  selectControl as select,
  settingField as field,
  settingsHeading,
  textControl as input,
  titleCase,
} from './settings_ui';
import type { AppState, VisualIdentityProfile } from './types';

export function identityImageButton(
  url: string,
  ariaLabel: string,
  alt: string,
  imageClass: string,
  onOpen: (url: string) => void,
): HTMLElement {
  return el('button', {
    class: 'identity-thumb-button',
    title: 'Open larger view',
    'aria-label': ariaLabel,
    onclick: () => onOpen(url),
  }, [el('img', { class: imageClass, src: url, alt })]);
}

export function identityAuditCard(events: AppState['identityEvents'][string]): HTMLElement {
  return el('div', { class: 'persona-card' }, [
    settingsHeading('Activity history', 'An owner-scoped audit of reference, profile, and comparison changes.'),
    events.length
      ? el('div', { class: 'identity-audit-list' }, events.slice(0, 30).map((event) =>
          el('div', { class: 'manager-row' }, [
            el('strong', { textContent: titleCase(event.action) }),
            el('span', { class: 'meta', textContent: formatDate(event.created_at) }),
          ]),
        ))
      : el('div', { class: 'meta', textContent: 'No visual identity activity has been recorded.' }),
  ]);
}

// Comparison is advisory (ADR 0031) and off by default, so its policy and its
// threshold sit with the verifier rather than in front of someone setting a
// persona up. `docs/settings-experience.md` says thresholds are advanced; this
// is where that becomes true.
export function identityComparisonPolicyCard(
  profile: VisualIdentityProfile,
  busy: boolean,
  save: () => void,
): HTMLElement {
  const failureLabels: Record<string, string> = {
    show_unverified: 'Show the image with an “unverified” label',
    block_claim: 'Hide the image when comparison fails',
  };
  return el('div', { class: 'persona-card' }, [
    settingsHeading(
      'Comparison outcome',
      'What to do with a finished image when the optional comparison service scores it below the threshold. None of this runs unless a comparison service is configured.',
    ),
    field('When optional comparison fails', select(profile.failure_policy, ['show_unverified', 'block_claim'], (value) => {
      profile.failure_policy = value as VisualIdentityProfile['failure_policy'];
    }, (value) => failureLabels[value] ?? value), 'This applies only after a configured comparison service evaluates a generated image.'),
    field('Comparison threshold', input(String(profile.acceptance_threshold), (value) => {
      profile.acceptance_threshold = boundedNumber(value, 0, 1, profile.acceptance_threshold);
    }, 'number'), 'A higher score is stricter. Calibrate this with representative generated images before enabling blocking.'),
    field('Maximum generation attempts', input(String(profile.max_generation_attempts), (value) => {
      profile.max_generation_attempts = Math.round(boundedNumber(value, 1, 10, profile.max_generation_attempts));
    }, 'number'), 'The cap on bounded generation or correction attempts for one request.'),
    el('button', {
      class: 'pill-btn',
      textContent: 'Save comparison outcome',
      disabled: busy,
      'data-testid': 'identity-comparison-policy-save',
      onclick: save,
    }),
  ]);
}

export function identityPersonaSelector(
  selectedId: string,
  personaIds: readonly string[],
  name: (id: string) => string,
  change: (value: string) => void,
): HTMLElement {
  return field(
    'Persona',
    select(selectedId, personaIds, change, name),
    'Choose whose face, recipes, kept pictures, and comparison history you want to manage.',
  );
}
