import type { ApiClient } from './api';
import { el, errorMessage } from './dom';
import { IdentityMediaPicker } from './identity_media_picker';
import { identityImageButton } from './identity_settings_components';
import type { SettingsDialogs } from './settings_contracts';
import { actionRow, choiceField, longField, pageHint, switchField } from './settings_page';
import { advancedSettings, settingsCard } from './settings_ui';
import type { AppState, IdentityReference, Persona, VisualIdentityProfile } from './types';

/**
 * A persona's face: one switch, and the photo.
 *
 * The identity machinery - consent, references and their review, the
 * conditioning mechanism, the fallback when no workflow exists, comparison
 * thresholds - used to be a settings area of its own, and setting a persona
 * up meant reading it. What a person means is simpler: this persona looks
 * like this photo. So that is the control, on the persona's own page, and
 * the machinery shows itself only when it genuinely needs a hand: a photo
 * that came from a generated picture and waits for approval, or a photo with
 * nothing installed that can use it.
 *
 * Nothing about the durable model changed. The switch is the consent grant
 * ADR 0011 requires, a photo from this device is attested in the same motion
 * and counts at once, a generated picture keeps its review wall, and what is
 * stored is what `docs/persona-visual-identity.md` describes. The same card
 * renders on Persona Pictures, so there is one face and not two.
 */

type Mechanism = VisualIdentityProfile['conditioning_mechanism'];
type Fallback = VisualIdentityProfile['conditioning_fallback'];

// ADR 0031: resemblance comes from a declared structural mechanism. Only the
// mechanisms this catalog can actually apply are offered; a value that can only
// block is worse than no choice, so with one there is nothing to choose.
const MECHANISM_LABELS: Record<Mechanism, string> = {
  reference_adapter: 'From the photo, while the picture is made',
  identity_pass: 'Make the picture, then put the face in',
};

const FALLBACK_LABELS: Record<Fallback, string> = {
  allow_unconditioned: 'Make the picture anyway, labelled as not the persona',
  require_conditioning: 'Wait: refuse until one is installed',
};

function offeredMechanisms(profile: VisualIdentityProfile): Mechanism[] {
  const available = profile.available_mechanisms ?? [];
  const current = profile.conditioning_mechanism ?? 'reference_adapter';
  // What is stored stays selectable even if its workflow was since removed, so
  // opening this card cannot silently change what a persona is set to.
  return available.includes(current) ? [...available] : [current, ...available];
}

export class PersonaFaceView {
  private attested = false;
  private readonly loading = new Set<string>();
  private readonly loadErrors = new Map<string, string>();
  private readonly picker: IdentityMediaPicker;

  constructor(
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly renderApp: () => void,
    private readonly dialogs: Pick<SettingsDialogs, 'prompt' | 'confirm'>,
    private readonly openIdentitySetup: (personaId: string) => void,
    /** What else belongs in the fold - comparison, history - rendered by whoever hosts the card. */
    private readonly moreContent: ((persona: Persona) => HTMLElement[]) | null,
    /** Runs after the profile changed, for a host that shows more than the face. */
    private readonly afterChange: (personaId: string) => Promise<void> = async () => undefined,
  ) {
    this.picker = new IdentityMediaPicker(renderApp, client, (url) => this.openImage(url));
  }

  /** Fetch the profile once; the card fills in when it arrives. */
  async load(personaId: string): Promise<void> {
    if (this.appState.identityProfiles[personaId] || this.loading.has(personaId)) return;
    this.loading.add(personaId);
    this.loadErrors.delete(personaId);
    try {
      this.appState.identityProfiles[personaId] = await this.client.visualIdentity(personaId);
    } catch (error) {
      this.loadErrors.set(personaId, errorMessage(error, 'The face could not be loaded.'));
    } finally {
      this.loading.delete(personaId);
      this.renderApp();
    }
  }

  node(persona: Persona): HTMLElement {
    const profile = this.appState.identityProfiles[persona.id];
    const busy = this.appState.identityBusy;
    if (!profile) {
      const failure = this.loadErrors.get(persona.id);
      return settingsCard([
        switchField('Looks like this photo', false, () => undefined, { disabled: true, testId: 'persona-face-switch' }),
        failure
          ? el('p', { class: 'meta', textContent: failure })
          : el('p', { class: 'meta', textContent: 'Checking…' }),
      ], 'persona-face', 'persona-face');
    }
    const enabled = profile.consent_status === 'granted';
    const live = profile.references.filter((reference) => reference.review_status !== 'deleted');
    const approved = live.filter((reference) => reference.review_status === 'approved');
    const pending = live.filter((reference) => reference.review_status === 'pending');
    const rejected = live.filter((reference) => reference.review_status === 'rejected');
    return settingsCard([
      switchField('Looks like this photo', enabled, (on) => void (on ? this.enable(persona) : this.disable(persona)), {
        hover: 'Pictures of this persona are made from the photo, once an identity workflow is installed. Turning it off deletes the stored photos.',
        testId: 'persona-face-switch',
        disabled: busy,
      }),
      enabled ? this.photos(persona, profile, approved, pending) : null,
      enabled ? this.needsAHand(persona, profile, approved) : null,
      enabled ? this.more(persona, profile, rejected) : null,
    ], 'persona-face', 'persona-face');
  }

  private photos(persona: Persona, profile: VisualIdentityProfile, approved: IdentityReference[], pending: IdentityReference[]): HTMLElement {
    const busy = this.appState.identityBusy;
    return el('div', { class: 'face-strip', 'data-testid': 'persona-face-photos' }, [
      ...approved.map((reference) => this.photo(persona, reference)),
      ...pending.map((reference) => this.pendingPhoto(persona, reference)),
      el('div', { class: 'face-add' }, [
        // One motion: tick the rights box, choose the photo, and it is added -
        // approved, because a file from this device with a fresh attestation is
        // the person's own deliberate act. Generated pictures keep the review
        // wall.
        el('label', { class: 'checkbox-row identity-attestation' }, [
          el('input', {
            type: 'checkbox',
            checked: this.attested,
            'data-testid': 'identity-attested',
            onchange: (event: Event) => {
              this.attested = (event.currentTarget as HTMLInputElement).checked;
              this.renderApp();
            },
          }),
          'I made this image or have permission to use it.',
        ]),
        el('label', { class: 'setting-row identity-file-row' }, [
          el('span', {
            textContent: busy ? 'Adding…' : this.attested ? (approved.length ? 'Add another photo' : 'Choose a photo') : 'Tick the box, then choose a photo',
          }),
          el('input', {
            type: 'file',
            accept: 'image/png,image/jpeg,image/webp',
            disabled: !this.attested || busy,
            'data-testid': 'identity-reference-file',
            onchange: (event: Event) => {
              const file = (event.currentTarget as HTMLInputElement).files?.[0] ?? null;
              if (file) void this.upload(persona, file);
            },
          }),
        ]),
        actionRow([
          el('button', {
            class: 'pill-btn',
            textContent: 'Use a generated picture',
            title: 'A picture this persona was drawn in, kept as the face. It waits for your approval.',
            disabled: busy,
            'data-testid': 'identity-reference-gallery-open',
            onclick: () => void this.picker.open('reference'),
          }),
        ]),
        this.picker.isOpen('reference')
          ? this.picker.node({
              mode: 'reference',
              actionLabel: 'Use as reference',
              actionDisabled: busy || !this.attested,
              blockedMessage: this.attested ? undefined : 'Tick the box above first.',
              onUse: (item) => this.fromMedia(persona, profile, item.id),
            })
          : null,
      ]),
    ]);
  }

  private photo(persona: Persona, reference: IdentityReference): HTMLElement {
    return el('div', { class: 'face-photo', 'data-testid': `identity-reference-${reference.id}` }, [
      reference.content_url
        ? identityImageButton(reference.content_url, `Open ${persona.name} reference image`, `${persona.name} reference`, 'face-photo-img', (url) => this.openImage(url))
        : el('div', { class: 'face-photo-img missing', textContent: '?' }),
      el('button', {
        class: 'face-photo-remove',
        textContent: '×',
        title: 'Remove this photo',
        'aria-label': 'Remove this photo',
        disabled: this.appState.identityBusy,
        onclick: () => void this.remove(persona, reference),
      }),
      reference.is_primary ? el('span', { class: 'face-photo-note', textContent: 'main' }) : null,
    ]);
  }

  /** A generated picture chosen as the face waits for a human to say it is one. */
  private pendingPhoto(persona: Persona, reference: IdentityReference): HTMLElement {
    return el('div', { class: 'face-photo pending', 'data-testid': `identity-reference-${reference.id}` }, [
      reference.content_url
        ? identityImageButton(reference.content_url, `Open ${persona.name} reference image`, `${persona.name} reference`, 'face-photo-img', (url) => this.openImage(url))
        : el('div', { class: 'face-photo-img missing', textContent: '?' }),
      el('span', { class: 'face-photo-note', textContent: `Is this ${persona.name}?` }),
      actionRow([
        el('button', { class: 'send-btn', textContent: 'Yes', disabled: this.appState.identityBusy, onclick: () => void this.approve(persona, reference) }),
        el('button', { class: 'pill-btn', textContent: 'No', disabled: this.appState.identityBusy, onclick: () => void this.reject(persona, reference) }),
      ]),
    ]);
  }

  /** The one line said out loud, and only when a hand is needed. */
  private needsAHand(persona: Persona, profile: VisualIdentityProfile, approved: IdentityReference[]): HTMLElement | null {
    if (!approved.length) return pageHint(`Add a photo to give ${persona.name} a face.`, 'persona-face-hint');
    if (profile.generation_workflow_configured) return null;
    return el('div', { class: 'face-needs', 'data-testid': 'persona-face-needs' }, [
      el('span', { textContent: 'Nothing can use the photo yet: no identity workflow is installed.' }),
      el('button', {
        class: 'pill-btn',
        textContent: 'Set it up',
        'data-testid': 'identity-configure-generation',
        onclick: () => this.openIdentitySetup(persona.id),
      }),
    ]);
  }

  private more(persona: Persona, profile: VisualIdentityProfile, rejected: IdentityReference[]): HTMLElement {
    const offered = offeredMechanisms(profile);
    return advancedSettings('More about the face', 'Appearance in words, how the face is made, and what happens without a workflow.', [
      longField(`How ${persona.name} looks`, profile.appearance_description, (value) => {
        profile.appearance_description = value;
      }, {
        hover: 'Stable details in words - hair, eyes, build - added to every prompt. The photo does the rest.',
        commit: () => void this.saveProfile(persona, profile),
      }),
      offered.length > 1
        ? choiceField('How the face is made', profile.conditioning_mechanism ?? 'reference_adapter', offered, (value) => {
            profile.conditioning_mechanism = value as Mechanism;
            void this.saveProfile(persona, profile);
          }, {
            display: (value) => MECHANISM_LABELS[value as Mechanism] ?? value,
            hover: 'Conditioning applies the photo while the picture is made. A later pass makes the picture first and then replaces the face; it works with any model but cannot change pose or lighting.',
            testId: 'identity-mechanism',
          })
        : el('div', { class: 'setting-row', title: 'The one technique this catalog can apply. Every picture records which technique produced its face.' }, [
            el('label', { textContent: 'How the face is made' }),
            el('span', { class: 'meta setting-value', 'data-testid': 'identity-mechanism', textContent: MECHANISM_LABELS[profile.conditioning_mechanism ?? 'reference_adapter'] }),
          ]),
      choiceField('Without an identity workflow', profile.conditioning_fallback ?? 'allow_unconditioned', ['allow_unconditioned', 'require_conditioning'], (value) => {
        profile.conditioning_fallback = value as Fallback;
        void this.saveProfile(persona, profile);
      }, {
        display: (value) => FALLBACK_LABELS[value as Fallback] ?? value,
        hover: 'A picture made anyway never claims to be the persona: it is labelled as unconditioned.',
        testId: 'identity-fallback',
      }),
      rejected.length
        ? el('div', { class: 'face-strip' }, rejected.map((reference) =>
            el('div', { class: 'face-photo rejected', 'data-testid': `identity-reference-${reference.id}` }, [
              reference.content_url
                ? identityImageButton(reference.content_url, `Open ${persona.name} reference image`, `${persona.name} reference`, 'face-photo-img', (url) => this.openImage(url))
                : el('div', { class: 'face-photo-img missing', textContent: '?' }),
              el('span', { class: 'face-photo-note', textContent: reference.rejection_reason || 'Not this persona' }),
              el('button', {
                class: 'face-photo-remove',
                textContent: '×',
                title: 'Delete this picture',
                'aria-label': 'Delete this picture',
                onclick: () => void this.remove(persona, reference),
              }),
            ])))
        : null,
      ...(this.moreContent?.(persona) ?? []),
    ], { testId: `persona-face-more-${persona.id}` });
  }

  private openImage(url: string): void {
    this.appState.chatImagePreview = url;
    this.renderApp();
  }

  private async enable(persona: Persona): Promise<void> {
    const confirmed = await this.dialogs.confirm(
      `Give ${persona.name} a face`,
      `Nice Assistant will store the photos you choose for ${persona.name} and make pictures from them. For a fictional persona this confirms your right to use the images; it does not claim the persona is a real person giving consent.`,
      'Turn on',
    );
    if (!confirmed) {
      this.renderApp();
      return;
    }
    await this.run(persona.id, async () => {
      this.appState.identityProfiles[persona.id] = await this.client.grantIdentityConsent(persona.id);
    });
  }

  private async disable(persona: Persona): Promise<void> {
    const confirmed = await this.dialogs.confirm(
      `Stop using ${persona.name}'s photos`,
      `${persona.name}'s stored photos are deleted and pictures are no longer made from them. The activity record stays; the photos cannot be restored.`,
      'Turn off and delete',
    );
    if (!confirmed) {
      this.renderApp();
      return;
    }
    await this.run(persona.id, async () => {
      this.appState.identityProfiles[persona.id] = await this.client.withdrawIdentityConsent(persona.id);
    });
  }

  private async upload(persona: Persona, file: File): Promise<void> {
    if (!this.attested) return;
    await this.run(persona.id, async () => {
      await this.client.uploadIdentityReference(persona.id, file, 'user_upload');
      this.attested = false;
      await this.reload(persona.id);
    });
  }

  private async fromMedia(persona: Persona, profile: VisualIdentityProfile, mediaId: string): Promise<void> {
    if (!this.attested || profile.consent_status !== 'granted') return;
    await this.run(persona.id, async () => {
      await this.client.identityReferenceFromMedia(persona.id, mediaId);
      this.attested = false;
      this.picker.close();
      await this.reload(persona.id);
    });
  }

  private async approve(persona: Persona, reference: IdentityReference): Promise<void> {
    await this.run(persona.id, async () => {
      await this.client.approveIdentityReference(reference.id);
      await this.reload(persona.id);
    });
  }

  private async reject(persona: Persona, reference: IdentityReference): Promise<void> {
    const reason = await this.dialogs.prompt('Not this persona', 'Why not? A word or two is enough.', `Does not look like ${persona.name}.`);
    if (reason === null) return;
    await this.run(persona.id, async () => {
      await this.client.rejectIdentityReference(reference.id, reason);
      await this.reload(persona.id);
    });
  }

  private async remove(persona: Persona, reference: IdentityReference): Promise<void> {
    const confirmed = await this.dialogs.confirm(
      'Remove this photo',
      'The stored photo is deleted. The removal stays in the activity record.',
      'Remove',
    );
    if (!confirmed) return;
    await this.run(persona.id, async () => {
      await this.client.deleteIdentityReference(reference.id);
      await this.reload(persona.id);
    });
  }

  private async saveProfile(persona: Persona, profile: VisualIdentityProfile): Promise<void> {
    await this.run(persona.id, async () => {
      try {
        this.appState.identityProfiles[persona.id] = await this.client.updateVisualIdentity(persona.id, profile);
      } catch (error) {
        // A refused save means the copy on screen is behind. Reload it, or the
        // next attempt is refused for the same reason forever.
        await this.reload(persona.id).catch(() => undefined);
        throw error;
      }
    });
  }

  private async reload(personaId: string): Promise<void> {
    this.appState.identityProfiles[personaId] = await this.client.visualIdentity(personaId);
    await this.afterChange(personaId);
  }

  private async run(personaId: string, action: () => Promise<void>): Promise<void> {
    this.appState.identityBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      await action();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, `${this.appState.personas.find((item) => item.id === personaId)?.name ?? 'The persona'}'s face could not be changed.`);
    } finally {
      this.appState.identityBusy = false;
      this.renderApp();
    }
  }
}
