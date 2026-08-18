import { el } from './dom';
import type { AppState } from './types';

/**
 * One picture, full screen, and a way to reach the others.
 *
 * `step` is absent for a preview that stands alone - a persona avatar, an
 * identity reference - and present for a picture opened from a conversation,
 * where the obvious next thing to want is the one before or after it.
 */
export function imageOverlay(
  url: string,
  alt: string,
  close: () => void,
  gallery?: { total: number; position: number; step: (delta: number) => void },
): HTMLElement {
  const stepper = gallery && gallery.total > 1 ? gallery : null;
  let touchStartX = 0;
  const arrow = (delta: number, label: string, glyph: string) =>
    el('button', {
      class: `icon-btn media-preview-step media-preview-step-${delta < 0 ? 'previous' : 'next'}`,
      textContent: glyph,
      title: label,
      'aria-label': label,
      'data-testid': `image-preview-${delta < 0 ? 'previous' : 'next'}`,
      onclick: (event: Event) => {
        event.stopPropagation();
        stepper?.step(delta);
      },
    });
  return el('div', { class: 'modal-backdrop media-preview-backdrop', 'data-testid': 'image-preview', onclick: close }, [
    el('div', {
      class: 'media-preview-frame',
      role: 'dialog',
      'aria-modal': 'true',
      'aria-label': `${alt} preview`,
      onclick: (event: Event) => event.stopPropagation(),
      ontouchstart: (event: Event) => {
        touchStartX = (event as TouchEvent).changedTouches[0]?.clientX ?? 0;
      },
      ontouchend: (event: Event) => {
        const endX = (event as TouchEvent).changedTouches[0]?.clientX ?? 0;
        const travelled = endX - touchStartX;
        // Short enough to feel responsive, long enough that a tap meant to
        // close the picture is never read as a swipe.
        if (Math.abs(travelled) < 45) return;
        stepper?.step(travelled < 0 ? 1 : -1);
      },
    }, [
      el('button', {
        class: 'icon-btn media-preview-close',
        textContent: '✕',
        title: 'Close preview',
        'aria-label': 'Close preview',
        onclick: close,
      }),
      ...(stepper ? [arrow(-1, 'Previous picture', '‹'), arrow(1, 'Next picture', '›')] : []),
      el('img', {
        class: 'media-preview-image',
        src: url,
        alt,
        title: 'Close preview',
        onclick: close,
      }),
      ...(stepper
        ? [el('div', {
            class: 'meta media-preview-count',
            'data-testid': 'image-preview-count',
            textContent: `${stepper.position + 1} of ${stepper.total}`,
          })]
        : []),
    ]),
  ]);
}

/**
 * Move to the picture before or after this one, wrapping at the ends.
 *
 * Arriving at a picture counts as asking to see it, so a blurred one is
 * revealed the same way tapping it would reveal it. The blur exists to keep
 * pictures out of the scroll-back, not to gate a preview somebody is already
 * looking at.
 */
export function stepChatImage(appState: AppState, delta: number): void {
  const gallery = appState.chatImageGallery;
  if (gallery.length < 2) return;
  const index = gallery.indexOf(appState.chatImagePreview);
  if (index < 0) return;
  const next = gallery[(index + delta + gallery.length) % gallery.length];
  if (!next) return;
  appState.chatImagePreview = next;
  appState.revealedImages[next] = true;
}

export function videoOverlay(appState: AppState, render: () => void): HTMLElement {
  const close = () => { appState.chatVideoPreview = ''; render(); };
  return el('div', { class: 'modal-backdrop media-preview-backdrop', 'data-testid': 'video-preview', onclick: close }, [
    el('div', {
      class: 'media-preview-frame video-preview-frame',
      role: 'dialog',
      'aria-modal': 'true',
      'aria-label': 'Video preview',
      onclick: (event: Event) => event.stopPropagation(),
    }, [
      el('button', {
        class: 'icon-btn media-preview-close',
        textContent: '✕',
        title: 'Close preview',
        'aria-label': 'Close preview',
        onclick: close,
      }),
      el('video', { class: 'video-preview-media', src: appState.chatVideoPreview, controls: true, autoplay: true }),
    ]),
  ]);
}
