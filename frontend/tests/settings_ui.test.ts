import { describe, expect, it } from 'vitest';

import { el } from '../src/dom';
import { infoTip, settingField } from '../src/settings_ui';

describe('approachable settings controls', () => {
  it('connects an info button to an accessible tooltip', () => {
    const tip = infoTip('A concise explanation.', 'About this setting');
    const button = tip.querySelector('button') as HTMLButtonElement;
    const tooltip = tip.querySelector('[role="tooltip"]') as HTMLElement;

    expect(button.getAttribute('aria-label')).toBe('About this setting');
    expect(button.getAttribute('aria-describedby')).toBe(tooltip.id);
    expect(tooltip.textContent).toBe('A concise explanation.');
  });

  it('associates setting labels with their controls', () => {
    const field = settingField('Service address', el('input'));
    const label = field.querySelector('label') as HTMLLabelElement;
    const input = field.querySelector('input') as HTMLInputElement;

    expect(input.id).not.toBe('');
    expect(label.htmlFor).toBe(input.id);
  });
});
