import { describe, expect, it, vi } from 'vitest';

import { imageOverlay, stepChatImage } from '../src/media_overlays';
import { createState } from '../src/state';

function openedAt(index: number, total = 3) {
  const appState = createState();
  appState.chatImageGallery = Array.from({ length: total }, (_, position) => `/api/v1/media/image-${position}`);
  appState.chatImagePreview = appState.chatImageGallery[index] as string;
  return appState;
}

describe('stepping between the pictures in a chat', () => {
  it('moves forward and back through them', () => {
    const appState = openedAt(0);

    stepChatImage(appState, 1);
    expect(appState.chatImagePreview).toBe('/api/v1/media/image-1');

    stepChatImage(appState, -1);
    expect(appState.chatImagePreview).toBe('/api/v1/media/image-0');
  });

  it('wraps at both ends rather than stopping dead', () => {
    const appState = openedAt(0);

    stepChatImage(appState, -1);
    expect(appState.chatImagePreview).toBe('/api/v1/media/image-2');

    stepChatImage(appState, 1);
    expect(appState.chatImagePreview).toBe('/api/v1/media/image-0');
  });

  it('reveals a blurred picture on arrival, the way tapping it would', () => {
    const appState = openedAt(0);

    stepChatImage(appState, 1);

    // Arriving at a picture is asking to see it. The blur keeps pictures out
    // of the scroll-back; it is not a second gate on a preview already open.
    expect(appState.revealedImages['/api/v1/media/image-1']).toBe(true);
  });

  it('does nothing when there is nowhere to go', () => {
    const alone = openedAt(0, 1);
    stepChatImage(alone, 1);
    expect(alone.chatImagePreview).toBe('/api/v1/media/image-0');

    // A preview opened from somewhere other than a conversation - a persona
    // avatar, an identity reference - has no gallery behind it.
    const detached = createState();
    detached.chatImagePreview = '/api/v1/media/avatar';
    stepChatImage(detached, 1);
    expect(detached.chatImagePreview).toBe('/api/v1/media/avatar');
  });

  it('leaves a picture that is not in the gallery alone', () => {
    const appState = openedAt(0);
    appState.chatImagePreview = '/api/v1/media/somewhere-else';

    stepChatImage(appState, 1);

    expect(appState.chatImagePreview).toBe('/api/v1/media/somewhere-else');
  });
});

describe('the full-screen picture', () => {
  it('offers a way through the others and says where you are', () => {
    const step = vi.fn();
    const node = imageOverlay('/api/v1/media/image-1', 'Image', vi.fn(), { total: 3, position: 1, step });

    expect(node.querySelector('[data-testid="image-preview-count"]')?.textContent).toBe('2 of 3');
    (node.querySelector('[data-testid="image-preview-next"]') as HTMLButtonElement).click();
    expect(step).toHaveBeenCalledWith(1);
    (node.querySelector('[data-testid="image-preview-previous"]') as HTMLButtonElement).click();
    expect(step).toHaveBeenCalledWith(-1);
  });

  it('shows no stepper for a picture that stands alone', () => {
    const node = imageOverlay('/api/v1/media/avatar', 'Persona avatar', vi.fn());

    expect(node.querySelector('[data-testid="image-preview-next"]')).toBeNull();
    expect(node.querySelector('[data-testid="image-preview-count"]')).toBeNull();
  });

  it('shows no stepper when the conversation holds only this one', () => {
    const node = imageOverlay('/api/v1/media/image-0', 'Image', vi.fn(), { total: 1, position: 0, step: vi.fn() });

    expect(node.querySelector('[data-testid="image-preview-next"]')).toBeNull();
  });

  it('stepping does not also close the picture', () => {
    const close = vi.fn();
    const node = imageOverlay('/api/v1/media/image-1', 'Image', close, { total: 3, position: 1, step: vi.fn() });

    (node.querySelector('[data-testid="image-preview-next"]') as HTMLButtonElement).click();

    // The backdrop and the picture both close on click, so an arrow that let
    // the event through would advance and then immediately dismiss.
    expect(close).not.toHaveBeenCalled();
  });
});
