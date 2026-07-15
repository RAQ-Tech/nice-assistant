import { el, type Child } from './dom';

let infoTipSequence = 0;

export function infoTip(text: string, label = 'More information'): HTMLElement {
  infoTipSequence += 1;
  const tooltipId = `settings-info-${infoTipSequence}`;
  return el('span', { class: 'info-tip' }, [
    el('button', {
      class: 'info-tip-trigger',
      type: 'button',
      ariaLabel: label,
      'aria-describedby': tooltipId,
      textContent: 'i',
    }),
    el('span', { class: 'info-tip-content', id: tooltipId, role: 'tooltip', textContent: text }),
  ]);
}

export function settingsHeading(title: string, help: string, level: 'h4' | 'strong' = 'h4'): HTMLElement {
  return el('div', { class: 'settings-heading-with-info' }, [
    el(level, { textContent: title }),
    infoTip(help, `About ${title}`),
  ]);
}

export function settingsCard(children: Child[], className = ''): HTMLElement {
  return el('div', { class: `persona-card settings-card ${className}`.trim() }, children);
}

export function settingsIntro(title: string, description: string): HTMLElement {
  return el('div', { class: 'settings-intro' }, [
    el('strong', { textContent: title }),
    el('p', { textContent: description }),
  ]);
}

export function advancedSettings(
  summary: string,
  description: string,
  children: Child[],
  options: { open?: boolean; testId?: string; onToggle?: (open: boolean) => void } = {},
): HTMLDetailsElement {
  const details = el('details', {
    class: 'settings-advanced persona-card',
    open: Boolean(options.open),
    'data-testid': options.testId,
    ontoggle: (event: Event) => options.onToggle?.((event.currentTarget as HTMLDetailsElement).open),
  }, [
    el('summary', {}, [
      el('strong', { textContent: summary }),
      el('span', { class: 'meta', textContent: 'Optional' }),
    ]),
    el('p', { class: 'meta', textContent: description }),
    ...children,
  ]);
  return details as HTMLDetailsElement;
}

export function readinessRow(
  label: string,
  message: string,
  state: 'ready' | 'attention' | 'off',
  help?: string,
): HTMLElement {
  return el('div', { class: 'settings-readiness-row' }, [
    el('span', { class: `settings-readiness-dot ${state}`, ariaHidden: true }),
    el('div', {}, [
      help ? settingsHeading(label, help, 'strong') : el('strong', { textContent: label }),
      el('span', { class: 'meta', textContent: message }),
    ]),
  ]);
}

export function settingField(label: string, control: HTMLElement | null, help?: string): HTMLElement {
  if (control && !control.id) control.id = `setting-control-${++infoTipSequence}`;
  return el('div', { class: 'setting-row' }, [
    el('div', { class: 'setting-label-line' }, [
      el('label', { textContent: label, htmlFor: control?.id ?? '' }),
      help ? infoTip(help, `About ${label}`) : null,
    ]),
    control,
  ]);
}

export function textAreaSetting(
  label: string,
  value: string,
  change: (value: string) => void,
  help?: string,
): HTMLElement {
  return settingField(label, el('textarea', {
    value,
    onchange: (event: Event) => change((event.currentTarget as HTMLTextAreaElement).value),
  }), help);
}

export function textControl(value: string, change: (value: string) => void, type = 'text'): HTMLInputElement {
  return el('input', {
    type,
    value,
    onchange: (event: Event) => change((event.currentTarget as HTMLInputElement).value),
  }) as HTMLInputElement;
}

export function selectControl(
  value: string,
  options: readonly string[],
  change: (value: string) => void,
  label: (value: string) => string = (item) => item,
): HTMLSelectElement {
  return el('select', { onchange: (event: Event) => change((event.currentTarget as HTMLSelectElement).value) },
    options.map((option) => el('option', { value: option, selected: option === value, textContent: label(option) })),
  ) as HTMLSelectElement;
}

export function boundedNumber(value: string, minimum: number, maximum: number, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(maximum, Math.max(minimum, parsed)) : fallback;
}

export function titleCase(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}
