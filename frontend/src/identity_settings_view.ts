import type { ApiClient } from './api';
import { el, errorMessage, formatBytes, formatDate } from './dom';
import { IdentityMediaPicker } from './identity_media_picker';
import { identityReadinessCard } from './identity_settings_components';
import {
  advancedSettings,
  boundedNumber,
  selectControl as select,
  settingField as field,
  settingsHeading,
  settingsIntro,
  textAreaSetting as textareaRow,
  textControl as input,
  titleCase as title,
} from './settings_ui';
import type {
  AppState,
  IdentityReference,
  IdentityValidationSettings,
  VisualIdentityProfile,
} from './types';

interface IdentityDialogs {
  prompt(title: string, message: string, initial?: string): Promise<string | null>;
  confirm(title: string, message: string, confirmText?: string): Promise<boolean>;
}

export class IdentitySettingsView {
  private selectedFile: File | null = null;
  private attested = false;
  private providerResult = '';
  private readonly mediaPicker: IdentityMediaPicker;
  private advancedOpen = false;

  constructor(
    private readonly renderApp: () => void,
    private readonly appState: AppState,
    private readonly client: ApiClient,
    private readonly dialogs: IdentityDialogs,
  ) {
    this.mediaPicker = new IdentityMediaPicker(renderApp, client);
  }

  async refresh(): Promise<void> {
    this.appState.identitySelectedPersonaId ??= this.appState.personas[0]?.id ?? null;
    const personaId = this.appState.identitySelectedPersonaId;
    this.appState.identityBusy = true;
    try {
      this.appState.identitySettings = await this.client.identitySettings();
      if (personaId) await this.reloadPersona(personaId);
      this.appState.settingsError = '';
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Visual identity settings could not be loaded.');
    } finally {
      this.appState.identityBusy = false;
      this.renderApp();
    }
  }

  nodes(): HTMLElement[] {
    const personaId = this.appState.identitySelectedPersonaId;
    const profile = personaId ? this.appState.identityProfiles[personaId] : null;
    return [
      settingsIntro(
        'Keep each persona visually recognizable',
        'Choose a persona, add a clear reference image, and approve it. Reference-aware image generation must be configured separately before the image can influence new generations.',
      ),
      this.personaSelector(),
      profile
        ? this.profileStack(profile)
        : el('div', { class: 'settings-empty-state', textContent: 'Choose a persona to manage its appearance.' }),
    ];
  }

  private personaSelector(): HTMLElement {
    return field(
      'Persona',
      select(
        this.appState.identitySelectedPersonaId ?? '',
        this.appState.personas.map((persona) => persona.id),
        (value) => {
          this.appState.identitySelectedPersonaId = value || null;
          this.mediaPicker.close();
          this.selectedFile = null;
          this.attested = false;
          void this.refresh();
        },
        (value) => this.personaName(value),
      ),
      'Choose whose reference images, appearance guidance, and validation history you want to manage.',
    );
  }

  private profileStack(profile: VisualIdentityProfile): HTMLElement {
    const enabled = profile.consent_status === 'granted';
    const name = this.personaName(profile.persona_id);
    const validations = this.appState.identityValidations[profile.persona_id] ?? [];
    const events = this.appState.identityEvents[profile.persona_id] ?? [];
    return el('div', { class: 'identity-profile-stack', 'data-testid': 'identity-profile-stack' }, [
      identityReadinessCard(profile, name, enabled),
      enabled ? this.referenceManager(profile, name) : this.enablementCard(profile.persona_id, name),
      enabled ? this.appearanceCard(profile, name) : null,
      advancedSettings(
        'Advanced identity controls and diagnostics',
        `These controls tune comparison and troubleshooting. They are not required to store ${name}’s reference image, and a verifier cannot make generated images look more like the reference.`,
        [
          this.generationPolicyCard(profile),
          this.providerCard(this.appState.identitySettings),
          this.validationManager(profile, validations),
          this.auditCard(events),
          enabled ? this.dangerCard(profile.persona_id, name) : null,
        ],
        {
          open: this.advancedOpen,
          testId: 'identity-advanced-settings',
          onToggle: (open) => { this.advancedOpen = open; },
        },
      ),
    ]);
  }

  private enablementCard(personaId: string, name: string): HTMLElement {
    return el('div', { class: 'persona-card settings-action-card' }, [
      settingsHeading(
        `Set up ${name}’s appearance`,
        `Nice Assistant will privately store reference images you choose for ${name}. For a fictional AI persona, this confirms that you created the image or have permission to use it.`,
      ),
      el('button', {
        class: 'send-btn',
        textContent: `Enable visual identity for ${name}`,
        disabled: this.appState.identityBusy,
        'data-testid': 'identity-enable',
        onclick: () => void this.enableIdentity(personaId, name),
      }),
    ]);
  }

  private referenceManager(profile: VisualIdentityProfile, name: string): HTMLElement {
    const references = profile.references.filter((reference) => reference.review_status !== 'deleted');
    return el('div', { class: 'persona-card', 'data-testid': 'identity-reference-manager' }, [
      settingsHeading(
        `${name}’s reference images`,
        'Use a clear image that represents how this persona should look. New images stay pending until you approve them.',
      ),
      el('label', { class: 'setting-row identity-file-row' }, [
        el('span', { textContent: 'Choose an image from this device' }),
        el('input', {
          type: 'file',
          accept: 'image/png,image/jpeg,image/webp',
          'data-testid': 'identity-reference-file',
          onchange: (event: Event) => {
            this.selectedFile = (event.currentTarget as HTMLInputElement).files?.[0] ?? null;
            this.renderApp();
          },
        }),
      ]),
      this.selectedFile
        ? el('div', { class: 'selected-file-note', textContent: `Selected: ${this.selectedFile.name}` })
        : null,
      el('label', { class: 'checkbox-row identity-attestation' }, [
        el('input', {
          type: 'checkbox',
          checked: this.attested,
          onchange: (event: Event) => {
            this.attested = (event.currentTarget as HTMLInputElement).checked;
            this.renderApp();
          },
        }),
        'I created this image or have permission to use it.',
      ]),
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: this.appState.identityBusy ? 'Adding…' : 'Add selected image',
          disabled: !this.selectedFile || !this.attested || this.appState.identityBusy,
          'data-testid': 'identity-reference-upload',
          onclick: () => void this.uploadReference(profile.persona_id),
        }),
        el('button', {
          class: 'pill-btn',
          textContent: 'Choose from generated images',
          disabled: this.appState.identityBusy,
          'data-testid': 'identity-reference-gallery-open',
          onclick: () => void this.mediaPicker.open('reference'),
        }),
      ]),
      this.mediaPicker.isOpen('reference')
        ? this.mediaPicker.node({
            mode: 'reference',
            actionLabel: 'Use as reference',
            actionDisabled: this.appState.identityBusy || !this.attested,
            blockedMessage: this.attested ? undefined : 'Confirm above that you created the image or have permission to use it.',
            onUse: (item) => this.referenceFromMedia(profile.persona_id, item.id),
          })
        : null,
      references.length
        ? el('div', { class: 'identity-reference-list' }, references.map((reference) => this.referenceCard(reference, name)))
        : el('div', { class: 'settings-empty-state', textContent: `No reference images have been added for ${name}.` }),
    ]);
  }

  private appearanceCard(profile: VisualIdentityProfile, name: string): HTMLElement {
    return el('div', { class: 'persona-card' }, [
      settingsHeading(
        'Appearance guidance',
        `Describe stable details that should remain recognizable as ${name}. This helps generation prompts but does not replace a reference-aware workflow.`,
      ),
      textareaRow(`How ${name} should look`, profile.appearance_description, (value) => {
        profile.appearance_description = value;
      }, 'Include stable traits such as hair, eyes, facial features, body type, and other defining details.'),
      el('button', {
        class: 'send-btn',
        textContent: 'Save appearance guidance',
        disabled: this.appState.identityBusy,
        onclick: () => void this.saveProfile(profile),
      }),
    ]);
  }

  private generationPolicyCard(profile: VisualIdentityProfile): HTMLElement {
    const failureLabels: Record<string, string> = {
      show_unverified: 'Show the image with an “unverified” label',
      block_claim: 'Hide the image when comparison fails',
    };
    return el('div', { class: 'persona-card' }, [
      settingsHeading(
        'Generation and comparison behavior',
        'These controls matter only when optional post-generation comparison is configured.',
      ),
      field('Maximum generation attempts', input(String(profile.max_generation_attempts), (value) => {
        profile.max_generation_attempts = Math.round(boundedNumber(value, 1, 10, profile.max_generation_attempts));
      }, 'number'), 'The maximum number of bounded generation or correction attempts for one request.'),
      field('When comparison fails', select(profile.failure_policy, ['show_unverified', 'block_claim'], (value) => {
        profile.failure_policy = value as VisualIdentityProfile['failure_policy'];
      }, (value) => failureLabels[value] ?? value), 'Choose whether a failed comparison is shown honestly or withheld.'),
      field('Comparison threshold', input(String(profile.acceptance_threshold), (value) => {
        profile.acceptance_threshold = boundedNumber(value, 0, 1, profile.acceptance_threshold);
      }, 'number'), 'A higher score is stricter. Calibrate this with representative generated images before enabling blocking.'),
      el('button', {
        class: 'pill-btn',
        textContent: 'Save advanced controls',
        disabled: this.appState.identityBusy,
        onclick: () => void this.saveProfile(profile),
      }),
    ]);
  }

  private providerCard(settings: IdentityValidationSettings | null): HTMLElement {
    if (!settings) return el('div', { class: 'persona-card', textContent: 'Verifier settings are unavailable.' });
    const enabled = settings.provider === 'compreface';
    return el('div', { class: 'persona-card identity-provider-card' }, [
      settingsHeading(
        'Optional identity comparison service',
        'A verifier compares a finished image with the approved reference. It does not improve generation, so leave it off until reference-aware generation is useful.',
      ),
      field('Comparison service', select(settings.provider, ['disabled', 'compreface'], (value) => {
        settings.provider = value as IdentityValidationSettings['provider'];
        this.renderApp();
      }, (value) => value === 'disabled' ? 'Off' : 'CompreFace'), 'CompreFace is a separately deployed LAN service used only for face comparison.'),
      enabled ? field('CompreFace service address', input(settings.base_url, (value) => { settings.base_url = value; }, 'url'), 'The private-LAN address of the CompreFace service.') : null,
      enabled ? field('CompreFace API key', input(settings.api_key, (value) => { settings.api_key = value; }, 'password'), 'A verification API key created in CompreFace and encrypted by Nice Assistant.') : null,
      enabled ? field('Stop waiting after (seconds)', input(String(settings.timeout_seconds), (value) => {
        settings.timeout_seconds = boundedNumber(value, 1, 120, settings.timeout_seconds);
      }, 'number'), 'Bounds each comparison request so an unavailable verifier cannot hang generation indefinitely.') : null,
      el('div', { class: 'chips' }, [
        el('button', {
          class: 'send-btn',
          textContent: this.appState.identityBusy ? 'Saving…' : 'Save comparison service',
          disabled: this.appState.identityBusy,
          'data-testid': 'identity-provider-save',
          onclick: () => void this.saveProvider(),
        }),
        enabled ? el('button', {
          class: 'pill-btn',
          textContent: this.appState.identityBusy ? 'Checking…' : 'Test connection',
          disabled: this.appState.identityBusy,
          onclick: () => void this.checkProvider(),
        }) : null,
        this.providerResult ? el('span', { class: 'provider-check-message', textContent: this.providerResult }) : null,
      ]),
    ]);
  }

  private validationManager(
    profile: VisualIdentityProfile,
    validations: AppState['identityValidations'][string],
  ): HTMLElement {
    const name = this.personaName(profile.persona_id);
    return el('div', { class: 'persona-card' }, [
      settingsHeading(
        'Manual comparison',
        'Choose one of your generated images and compare its face with the approved reference without changing the image.',
      ),
      el('p', {
        class: 'meta',
        textContent: profile.validation_ready
          ? `Choose a generated image to compare with ${name}’s approved reference.`
          : 'Manual comparison becomes available after an approved reference and comparison service are configured.',
      }),
      el('button', {
        class: 'pill-btn',
        textContent: 'Choose an image to compare',
        disabled: !profile.validation_ready || this.appState.identityBusy,
        'data-testid': 'identity-validation-gallery-open',
        onclick: () => void this.mediaPicker.open('validation'),
      }),
      this.mediaPicker.isOpen('validation')
        ? this.mediaPicker.node({
            mode: 'validation',
            actionLabel: 'Compare image',
            actionDisabled: this.appState.identityBusy,
            onUse: (item) => this.validateMedia(profile.persona_id, item.id),
          })
        : null,
      validations.length ? el('div', { class: 'identity-validation-list' }, validations.map((validation) =>
        el('div', { class: 'identity-validation-card' }, [
          el('img', {
            class: 'identity-validation-thumb',
            src: this.client.mediaUrl(validation.candidate_media_id),
            alt: `Compared with ${name}`,
          }),
          el('div', {}, [
            el('strong', { textContent: validation.claim_status === 'verified' ? 'Looks like the persona' : title(validation.claim_status) }),
            el('div', {
              class: 'meta',
              textContent: `${formatDate(validation.created_at)}${validation.score === null ? '' : ` · ${(validation.score * 100).toFixed(1)}% match`}`,
            }),
            validation.error ? el('div', { class: 'provider-check-message', textContent: validation.error.message }) : null,
          ]),
        ]),
      )) : el('div', { class: 'meta', textContent: 'No images have been compared manually.' }),
    ]);
  }

  private referenceCard(reference: IdentityReference, name: string): HTMLElement {
    const status = reference.review_status === 'approved'
      ? (reference.is_primary ? 'Primary reference' : 'Approved reference')
      : (reference.review_status === 'pending' ? 'Needs your approval' : 'Rejected reference');
    return el('div', { class: 'identity-reference-card', 'data-testid': `identity-reference-${reference.id}` }, [
      reference.content_url
        ? el('button', {
            class: 'identity-thumb-button',
            title: 'Open larger view',
            onclick: () => window.open(reference.content_url as string, '_blank', 'noopener'),
          }, [el('img', { class: 'identity-reference-thumb', src: reference.content_url, alt: `${name} reference` })])
        : el('div', { class: 'identity-reference-thumb missing', textContent: 'Unavailable' }),
      el('div', { class: 'identity-reference-detail' }, [
        el('strong', { textContent: status }),
        el('div', { class: 'meta', textContent: `${reference.width}×${reference.height} · ${formatBytes(reference.byte_size)}` }),
        reference.review_status === 'pending'
          ? el('div', { class: 'meta', textContent: `Approve this only if it is a good representation of ${name}.` })
          : null,
        reference.rejection_reason ? el('div', { class: 'provider-check-message', textContent: reference.rejection_reason }) : null,
        el('div', { class: 'chips' }, [
          reference.review_status === 'pending'
            ? el('button', { class: 'send-btn', textContent: `Approve as ${name}`, onclick: () => void this.approveReference(reference.id) })
            : null,
          reference.review_status === 'pending'
            ? el('button', { class: 'pill-btn', textContent: 'Not this persona', onclick: () => void this.rejectReference(reference.id) })
            : null,
          el('button', { class: 'icon-btn danger', textContent: 'Delete', onclick: () => void this.deleteReference(reference.id) }),
        ]),
      ]),
    ]);
  }

  private auditCard(events: AppState['identityEvents'][string]): HTMLElement {
    return el('div', { class: 'persona-card' }, [
      settingsHeading('Activity history', 'An owner-scoped audit of reference, profile, and comparison changes.'),
      events.length
        ? el('div', { class: 'identity-audit-list' }, events.slice(0, 30).map((event) =>
            el('div', { class: 'manager-row' }, [
              el('strong', { textContent: title(event.action) }),
              el('span', { class: 'meta', textContent: formatDate(event.created_at) }),
            ]),
          ))
        : el('div', { class: 'meta', textContent: 'No visual identity activity has been recorded.' }),
    ]);
  }

  private dangerCard(personaId: string, name: string): HTMLElement {
    return el('div', { class: 'persona-card settings-danger-zone' }, [
      el('h4', { textContent: 'Remove visual identity data' }),
      el('p', { class: 'meta', textContent: `This permanently deletes ${name}’s stored reference files and cancels active comparisons.` }),
      el('button', {
        class: 'pill-btn danger',
        textContent: `Disable and delete ${name}’s references`,
        disabled: this.appState.identityBusy,
        onclick: () => void this.disableIdentity(personaId, name),
      }),
    ]);
  }

  private async saveProvider(): Promise<void> {
    const settings = this.appState.identitySettings;
    if (!settings) return;
    await this.run(async () => {
      this.appState.identitySettings = await this.client.updateIdentitySettings(settings);
      this.providerResult = settings.provider === 'disabled' ? 'Comparison is off.' : 'Comparison service saved.';
      const personaId = this.appState.identitySelectedPersonaId;
      if (personaId) await this.reloadPersona(personaId);
    });
  }

  private async checkProvider(): Promise<void> {
    await this.run(async () => {
      const result = await this.client.checkIdentityProvider();
      this.providerResult = `${title(String(result.status))}: ${String(result.message ?? '')}`;
    });
  }

  private async saveProfile(profile: VisualIdentityProfile): Promise<void> {
    await this.run(async () => {
      this.appState.identityProfiles[profile.persona_id] = await this.client.updateVisualIdentity(profile.persona_id, profile);
    });
  }

  private async enableIdentity(personaId: string, name: string): Promise<void> {
    const confirmed = await this.dialogs.confirm(
      'Enable persistent visual identity',
      `Nice Assistant will store reference images you choose for ${name} and may compare generated images with them. For fictional personas, this confirms your right to use the images; it does not claim the persona is a real person giving consent.`,
      'Enable visual identity',
    );
    if (!confirmed) return;
    await this.run(async () => { this.appState.identityProfiles[personaId] = await this.client.grantIdentityConsent(personaId); });
  }

  private async disableIdentity(personaId: string, name: string): Promise<void> {
    const confirmed = await this.dialogs.confirm(
      'Disable visual identity and delete references',
      `Permanently delete all stored reference files for ${name} and cancel active comparisons? The activity record remains, but the images cannot be restored.`,
      'Disable and delete',
    );
    if (!confirmed) return;
    await this.run(async () => { this.appState.identityProfiles[personaId] = await this.client.withdrawIdentityConsent(personaId); });
  }

  private async uploadReference(personaId: string): Promise<void> {
    if (!this.selectedFile || !this.attested) {
      this.appState.settingsError = 'Choose an image and confirm that you have permission to use it.';
      this.renderApp();
      return;
    }
    await this.run(async () => {
      await this.client.uploadIdentityReference(personaId, this.selectedFile as File, 'user_upload');
      this.selectedFile = null;
      this.attested = false;
      await this.reloadPersona(personaId);
    });
  }

  private async referenceFromMedia(personaId: string, mediaId: string): Promise<void> {
    if (!this.attested) return;
    await this.run(async () => {
      await this.client.identityReferenceFromMedia(personaId, mediaId);
      this.attested = false;
      this.mediaPicker.close();
      await this.reloadPersona(personaId);
    });
  }

  private async approveReference(referenceId: string): Promise<void> {
    const personaId = this.appState.identitySelectedPersonaId;
    if (!personaId) return;
    await this.run(async () => { await this.client.approveIdentityReference(referenceId); await this.reloadPersona(personaId); });
  }

  private async rejectReference(referenceId: string): Promise<void> {
    const personaId = this.appState.identitySelectedPersonaId;
    if (!personaId) return;
    const reason = await this.dialogs.prompt('Reject reference', 'Why is this not a good representation of the persona?', 'Does not represent this persona.');
    if (reason === null) return;
    await this.run(async () => { await this.client.rejectIdentityReference(referenceId, reason); await this.reloadPersona(personaId); });
  }

  private async deleteReference(referenceId: string): Promise<void> {
    const personaId = this.appState.identitySelectedPersonaId;
    if (!personaId) return;
    const confirmed = await this.dialogs.confirm(
      'Delete reference image',
      'Permanently delete this stored reference image? The deletion will remain in the activity history.',
      'Delete image',
    );
    if (!confirmed) return;
    await this.run(async () => { await this.client.deleteIdentityReference(referenceId); await this.reloadPersona(personaId); });
  }

  private async validateMedia(personaId: string, mediaId: string): Promise<void> {
    await this.run(async () => {
      const accepted = await this.client.validateIdentityMedia(personaId, mediaId);
      this.mediaPicker.close();
      await this.waitJob(accepted.job.id);
      await this.reloadPersona(personaId);
    });
  }

  private async waitJob(jobId: string): Promise<void> {
    const deadline = Date.now() + 120_000;
    while (Date.now() < deadline) {
      const job = await this.client.job(jobId);
      if (['completed', 'failed', 'cancelled'].includes(job.status)) return;
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    throw new Error('The comparison is still running. Reopen this section later to see the result.');
  }

  private async reloadPersona(personaId: string): Promise<void> {
    const [profile, validations, history] = await Promise.all([
      this.client.visualIdentity(personaId),
      this.client.identityValidations(personaId),
      this.client.identityHistory(personaId),
    ]);
    this.appState.identityProfiles[personaId] = profile;
    this.appState.identityValidations[personaId] = validations.items;
    this.appState.identityEvents[personaId] = history.items;
  }

  private async run(action: () => Promise<void>): Promise<void> {
    this.appState.identityBusy = true;
    this.appState.settingsError = '';
    this.renderApp();
    try {
      await action();
    } catch (error) {
      this.appState.settingsError = errorMessage(error, 'Visual identity operation failed.');
    } finally {
      this.appState.identityBusy = false;
      this.renderApp();
    }
  }

  private personaName(personaId: string): string {
    return this.appState.personas.find((persona) => persona.id === personaId)?.name ?? 'Persona';
  }
}
