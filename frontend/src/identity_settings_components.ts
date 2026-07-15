import { el } from './dom';
import { readinessRow } from './settings_ui';
import type { VisualIdentityProfile } from './types';

export function identityReadinessCard(
  profile: VisualIdentityProfile,
  name: string,
  enabled: boolean,
): HTMLElement {
  const hasReference = profile.approved_reference_count > 0;
  const blocksAutomatically = profile.validation_ready && profile.failure_policy === 'block_claim';
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
          : 'Not configured; add an identity-aware ComfyUI workflow in Media Catalog',
        profile.generation_workflow_configured ? 'ready' : 'attention',
        'Generation needs an enabled ComfyUI model and a compatible workflow with explicit identity-image bindings.',
      ),
      readinessRow(
        'Optional comparison',
        profile.verification_configured ? 'Verifier settings are configured' : 'Off; generated images will remain unverified',
        profile.verification_configured ? 'ready' : 'off',
        'An optional verifier can compare a finished face with the reference. It cannot improve generation.',
      ),
      readinessRow(
        'Automatic blocking',
        blocksAutomatically ? 'Images that fail comparison are hidden' : 'Off',
        blocksAutomatically ? 'attention' : 'off',
        'When enabled, a generated image that fails comparison is withheld instead of being presented as the persona.',
      ),
    ]),
  ]);
}
