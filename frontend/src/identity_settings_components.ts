import { el } from './dom';
import { readinessRow } from './settings_ui';
import type { VisualIdentityProfile } from './types';

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
