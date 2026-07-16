import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { ChatRenderer } from '../src/chat_rendering';
import type { MediaController } from '../src/media';
import type { PlaybackController } from '../src/playback';
import { SETTINGS_DEFAULTS } from '../src/settings';
import { createState } from '../src/state';
import type { CapabilityRequest, ChatAttachment, Message } from '../src/types';

function attachment(status: ChatAttachment['status'] = 'completed'): ChatAttachment {
  return {
    id: 'attachment-1',
    kind: 'image',
    status,
    capability_request_id: 'capability-1',
    media_id: status === 'completed' ? 'media-1' : null,
    content_url: status === 'completed' ? '/api/v1/media/media-1' : null,
    identity_state: 'unconditioned',
    safe_error: status === 'failed' ? 'That picture could not be made.' : null,
    retry_available: status === 'failed',
    created_at: 1,
    updated_at: 2,
    completed_at: status === 'completed' ? 2 : null,
  };
}

function request(item: ChatAttachment): CapabilityRequest {
  return {
    id: item.capability_request_id,
    capability_key: 'media.generate_image',
    status: item.status === 'completed' ? 'completed' : item.status === 'failed' ? 'failed' : 'running',
    permission_mode: 'auto',
    arguments: { prompt: 'a moonlit garden' },
    result: null,
    error: null,
    chat_id: 'chat-1',
    turn_id: 'turn-1',
    assistant_message_id: 'message-1',
    job_id: 'job-1',
    requested_at: 1,
    decided_at: null,
    started_at: 1,
    completed_at: item.completed_at,
    expires_at: null,
    retry_of_request_id: null,
    attachment: item,
    media_plan: null,
  };
}

function message(item: ChatAttachment): Message {
  return {
    id: 'message-1',
    role: 'assistant',
    text: '',
    created_at: 1,
    attachments: [item],
  };
}

describe('durable chat attachments', () => {
  it('opens a completed picture immediately when blur is off', () => {
    const appState = createState();
    const item = attachment();
    appState.settings = { ...SETTINGS_DEFAULTS, chat_blur_images: false };
    appState.capabilityRequests = [request(item)];
    const render = vi.fn();
    const renderer = new ChatRenderer(
      {} as MediaController,
      {} as PlaybackController,
      render,
      appState,
      {} as ApiClient,
    );
    const node = renderer.message(message(item), null)!;
    const image = node.querySelector('.attachment-image') as HTMLImageElement;

    expect(image.classList.contains('image-blurred')).toBe(false);
    image.click();
    expect(appState.chatImagePreview).toContain('/api/v1/media/media-1');
  });

  it('uses reveal then preview when the persisted blur preference is on', () => {
    const appState = createState();
    const item = attachment();
    appState.settings = { ...SETTINGS_DEFAULTS, chat_blur_images: true };
    appState.capabilityRequests = [request(item)];
    const renderer = new ChatRenderer(
      {} as MediaController,
      {} as PlaybackController,
      () => undefined,
      appState,
      {} as ApiClient,
    );
    const node = renderer.message(message(item), null)!;
    const image = node.querySelector('.attachment-image') as HTMLImageElement;

    expect(image.classList.contains('image-blurred')).toBe(true);
    image.click();
    expect(image.classList.contains('image-blurred')).toBe(false);
    expect(appState.chatImagePreview).toBe('');
    image.click();
    expect(appState.chatImagePreview).toContain('/api/v1/media/media-1');
  });

  it('shows a compact retry action for a failed picture', () => {
    const appState = createState();
    const item = attachment('failed');
    appState.settings = { ...SETTINGS_DEFAULTS };
    appState.capabilityRequests = [request(item)];
    const renderer = new ChatRenderer(
      {} as MediaController,
      {} as PlaybackController,
      () => undefined,
      appState,
      {} as ApiClient,
    );
    const node = renderer.message(message(item), null)!;

    expect(node.textContent).toContain('That picture could not be made.');
    expect(node.querySelector('[data-testid="retry-chat-attachment"]')).not.toBeNull();
    expect(node.textContent).not.toContain('Media plan');
  });
});
