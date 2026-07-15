import { el, type Child } from './dom';

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

export function readinessRow(label: string, message: string, state: 'ready' | 'attention' | 'off'): HTMLElement {
  return el('div', { class: 'settings-readiness-row' }, [
    el('span', { class: `settings-readiness-dot ${state}`, ariaHidden: true }),
    el('div', {}, [el('strong', { textContent: label }), el('span', { class: 'meta', textContent: message })]),
  ]);
}

export function settingField(label: string, control: HTMLElement | null): HTMLElement {
  return el('label', { class: 'setting-row' }, [el('span', { textContent: label }), control]);
}

export function textAreaSetting(label: string, value: string, change: (value: string) => void): HTMLElement {
  return settingField(label, el('textarea', {
    value,
    onchange: (event: Event) => change((event.currentTarget as HTMLTextAreaElement).value),
  }));
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
