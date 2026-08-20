import { describe, expect, it } from 'vitest';

import { avatarErrorFallback, avatarSource, monogramAvatar, monogramInitials } from '../src/avatar';

describe('a face for a persona with no picture', () => {
  it('takes initials the way a phone contact card does', () => {
    expect(monogramInitials('April')).toBe('A');
    expect(monogramInitials('Nova Prime')).toBe('NP');
    expect(monogramInitials('  april  ')).toBe('A');
    // Never empty: an empty monogram would be a colored square with no clue.
    expect(monogramInitials('')).toBe('?');
  });

  it('gives the same persona the same face on every screen', () => {
    // Deterministic, so April is one shade everywhere rather than reshuffling
    // per render or per device.
    expect(monogramAvatar('April')).toBe(monogramAvatar('April'));
    expect(monogramAvatar('April')).not.toBe(monogramAvatar('Bruce'));
  });

  it('is a complete image needing no network at all', () => {
    const url = monogramAvatar('April');

    expect(url.startsWith('data:image/svg+xml')).toBe(true);
    expect(decodeURIComponent(url)).toContain('>A</text>');
  });

  it('prefers the real picture whenever there is one', () => {
    expect(avatarSource('April', '/api/v1/personas/p1/avatar?v=abc')).toBe('/api/v1/personas/p1/avatar?v=abc');
    expect(avatarSource('April', '')).toBe(monogramAvatar('April'));
    expect(avatarSource('April', null)).toBe(monogramAvatar('April'));
  });

  it('replaces a picture that fails to load, once', () => {
    document.body.innerHTML = '<img src="http://gone.invalid/face.png">';
    const image = document.querySelector('img') as HTMLImageElement;
    image.addEventListener('error', avatarErrorFallback('April'));

    // The rotted-link case: the URL exists in the row, and nothing answers.
    image.dispatchEvent(new Event('error'));
    const swapped = image.src;
    image.dispatchEvent(new Event('error'));

    expect(swapped).toBe(monogramAvatar('April'));
    // A second failure must not loop the handler.
    expect(image.src).toBe(swapped);
  });
});
