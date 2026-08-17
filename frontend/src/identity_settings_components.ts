import { el, formatDate } from './dom';
import {
  boundedNumber,
  readinessRow,
  selectControl as select,
  settingField as field,
  settingsHeading,
  textControl as input,
  titleCase,
} from './settings_ui';
import type { AppState, VisualIdentityProfile } from './types';

type Mechanism = VisualIdentityProfile['conditioning_mechanism'];

// ADR 0031: resemblance comes from a declared structural mechanism, not from
// comparing the result afterwards. The server declares two; only the first is
// implemented, so only the first is offered. A control that lets someone pick a
// value which can only block is worse than no control.
const MECHANISM_LABELS: Record<Mechanism, string> = {
  reference_adapter: 'Condition generation on the reference image',
  identity_pass: 'Replace the face after generation',
};
const AVAILABLE_MECHANISMS: readonly Mechanism[] = ['reference_adapter'];

const MECHANISM_HELP =
  'How the persona’s face is produced. Conditioning applies the approved reference while the image is being made, using an identity-capable ComfyUI workflow configured in Media Catalog. Every picture records which technique produced its face.';

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

export function identityReadinessCard(
  profile: VisualIdentityProfile,
  name: string,
  enabled: boolean,
  configureGeneration: () => void = () => undefined,
  configureComparison: () => void = () => undefined,
): HTMLElement {
  const hasReference = profile.approved_reference_count > 0;
  const requiresConditioning = profile.conditioning_fallback === 'require_conditioning';
  return el('div', { class: 'persona-card identity-readiness-card' }, [
    el('div', { class: 'task-model-head' }, [
      el('div', {}, [
        el('strong', { textContent: `${name} visual identity` }),
        el('div', { class: 'meta', textContent: 'A quick view of what is actually ready.' }),
      ]),
      el('span', {
        class: `provider-status ${enabled ? 'ok' : 'fail'}`,
        textContent: enabled ? 'Enabled' : 'Not enabled',
      }),
    ]),
    el('div', { class: 'settings-readiness-list' }, [
      readinessRow(
        'Reference image',
        hasReference ? `${profile.approved_reference_count} approved` : 'Add and approve at least one image',
        hasReference ? 'ready' : 'attention',
        'A reviewed image that defines how this persona should look. References remain private protected media.',
      ),
      readinessRow(
        'Reference-aware generation',
        profile.generation_workflow_configured
          ? 'An identity-capable ComfyUI workflow is configured'
          : 'Not configured; ComfyUI needs an identity model plus a bound workflow in Media Catalog',
        profile.generation_workflow_configured ? 'ready' : 'attention',
        'Install and test an identity graph such as IPAdapter, InstantID, PuLID, or PhotoMaker in ComfyUI. Then add its API-format workflow in Media Catalog with feature identity_control and explicit identity_image_bindings.',
      ),
      readinessRow(
        'Optional comparison',
        profile.verification_configured ? 'Verifier settings are configured' : 'Off; generated images will remain unverified',
        profile.verification_configured ? 'ready' : 'off',
        'An optional verifier can compare a finished face with the reference. It cannot improve generation.',
      ),
      readinessRow(
        'When identity control is unavailable',
        requiresConditioning
          ? 'Block the request until reference-aware generation is ready'
          : 'Allow a clearly labeled unconditioned image',
        requiresConditioning ? 'attention' : 'off',
        'This controls pre-generation fallback when no compatible identity workflow can run. It is separate from face comparison after generation.',
      ),
      readinessRow(
        'When comparison fails',
        profile.failure_policy === 'block_claim'
          ? 'Hide the failed image'
          : 'Show the image with an unverified label',
        profile.failure_policy === 'block_claim' ? 'attention' : 'off',
        profile.verification_configured
          ? 'This policy applies after the optional comparison service evaluates a generated image.'
          : 'This saved policy will take effect only if the optional comparison service is configured later.',
      ),
    ]),
    el('div', { class: 'chips' }, [
      el('button', {
        class: 'pill-btn',
        textContent: profile.generation_workflow_configured ? 'Review identity control setup' : 'Set up identity control',
        'data-testid': 'identity-configure-generation',
        onclick: configureGeneration,
      }),
      el('button', {
        class: 'pill-btn',
        textContent: profile.verification_configured ? 'Review optional comparison' : 'Configure optional comparison',
        'data-testid': 'identity-configure-comparison',
        onclick: configureComparison,
      }),
    ]),
  ]);
}


function mechanismField(profile: VisualIdentityProfile, changed: () => void): HTMLElement {
  const current = profile.conditioning_mechanism ?? 'reference_adapter';
  if (AVAILABLE_MECHANISMS.length < 2) {
    // Say plainly what will happen rather than offering a choice of one.
    return field(
      'How the face is produced',
      el('span', { class: 'meta', 'data-testid': 'identity-mechanism', textContent: MECHANISM_LABELS[current] }),
      MECHANISM_HELP,
    );
  }
  return field('How the face is produced', select(current, AVAILABLE_MECHANISMS, (value) => {
    profile.conditioning_mechanism = value as Mechanism;
    changed();
  }, (value) => MECHANISM_LABELS[value as Mechanism] ?? value), MECHANISM_HELP);
}

export function identityGenerationPolicyCard(
  profile: VisualIdentityProfile,
  busy: boolean,
  save: () => void,
  changed: () => void = () => undefined,
): HTMLElement {
  const fallbackLabels: Record<VisualIdentityProfile['conditioning_fallback'], string> = {
    allow_unconditioned: 'Generate and label an unconditioned image',
    require_conditioning: 'Block until identity control is ready',
  };
  return el('div', { class: 'persona-card' }, [
    settingsHeading(
      'Identity generation behavior',
      'How the persona’s face is produced, and what happens when that is not possible. What happens after generation, when the optional comparison service disagrees, is in the advanced section.',
    ),
    mechanismField(profile, changed),
    field('When identity control is unavailable', select(
      profile.conditioning_fallback ?? 'allow_unconditioned',
      ['allow_unconditioned', 'require_conditioning'],
      (value) => {
        profile.conditioning_fallback = value as VisualIdentityProfile['conditioning_fallback'];
      },
      (value) => fallbackLabels[value as VisualIdentityProfile['conditioning_fallback']] ?? value,
    ), 'Allowing fallback never claims a match: the resulting image is explicitly labeled unconditioned and may not resemble the persona.'),
    field('Maximum generation attempts', input(String(profile.max_generation_attempts), (value) => {
      profile.max_generation_attempts = Math.round(boundedNumber(value, 1, 10, profile.max_generation_attempts));
    }, 'number'), 'The maximum number of bounded generation or correction attempts for one request.'),
    el('button', {
      class: 'pill-btn',
      textContent: 'Save identity behavior',
      disabled: busy,
      'data-testid': 'identity-behavior-save',
      onclick: save,
    }),
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
    'Choose whose reference images, appearance guidance, kept pictures, and validation history you want to manage.',
  );
}
