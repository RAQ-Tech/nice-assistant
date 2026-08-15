import { describe, expect, it, vi } from 'vitest';

import type { ApiClient } from '../src/api';
import { ChatRenderer } from '../src/chat_rendering';
import { generationLogOverlay } from '../src/generation_log_view';
import type { MediaController } from '../src/media';
import type { PlaybackController } from '../src/playback';
import { SETTINGS_DEFAULTS } from '../src/settings';
import { createState } from '../src/state';
import type { CapabilityRequest, ChatAttachment, MediaJournal, Message } from '../src/types';

function attachment(): ChatAttachment {
  return {
    id: 'attachment-1',
    kind: 'image',
    status: 'completed',
    capability_request_id: 'capability-1',
    media_id: 'media-1',
    content_url: '/api/v1/media/media-1',
    identity_state: 'unconditioned',
    safe_error: null,
    retry_available: false,
    created_at: 1,
    updated_at: 2,
    completed_at: 2,
  };
}

function request(item: ChatAttachment): CapabilityRequest {
  return {
    id: item.capability_request_id,
    capability_key: 'media.generate_image',
    status: 'completed',
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
    completed_at: 2,
    expires_at: null,
    retry_of_request_id: null,
    attachment: item,
    media_plan: null,
  };
}

function message(item: ChatAttachment): Message {
  return { id: 'message-1', role: 'assistant', text: '', created_at: 1, attachments: [item] };
}

function journal(): MediaJournal {
  return {
    id: 'journal-1',
    kind: 'image',
    origin: 'conversation',
    status: 'completed',
    media_id: 'media-1',
    started_at: 1_760_000_000,
    completed_at: 1_760_000_012,
    duration_ms: 12_000,
    error: null,
    stages: [
      {
        sequence: 1,
        stage: 'provider_request',
        status: 'ok',
        summary: 'submitting to local',
        detail: { backend: 'comfyui' },
        started_at: 1_760_000_001,
        duration_ms: 900,
      },
    ],
  };
}

describe('generation log', () => {
  it('reaches the log in one click from the picture itself', async () => {
    const appState = createState();
    const item = attachment();
    appState.settings = { ...SETTINGS_DEFAULTS };
    appState.capabilityRequests = [request(item)];
    const mediaJournalForMedia = vi.fn().mockResolvedValue(journal());
    const renderer = new ChatRenderer(
      {} as MediaController,
      {} as PlaybackController,
      () => undefined,
      appState,
      { mediaJournalForMedia } as unknown as ApiClient,
    );

    const node = renderer.message(message(item), null)!;
    const button = node.querySelector('[data-testid="open-generation-log"]') as HTMLButtonElement;
    expect(button).toBeTruthy();

    button.click();
    await vi.waitFor(() => expect(appState.generationLog?.id).toBe('journal-1'));
    expect(mediaJournalForMedia).toHaveBeenCalledWith('media-1');
  });

  it('explains a picture with no recorded log instead of failing silently', async () => {
    const appState = createState();
    const item = attachment();
    appState.settings = { ...SETTINGS_DEFAULTS };
    appState.capabilityRequests = [request(item)];
    const renderer = new ChatRenderer(
      {} as MediaController,
      {} as PlaybackController,
      () => undefined,
      appState,
      { mediaJournalForMedia: vi.fn().mockRejectedValue(new Error('not found')) } as unknown as ApiClient,
    );

    const node = renderer.message(message(item), null)!;
    (node.querySelector('[data-testid="open-generation-log"]') as HTMLButtonElement).click();
    await vi.waitFor(() => expect(appState.uiError).toContain('No generation log'));
    expect(appState.generationLog).toBeNull();
  });

  it('shows every stage and offers the log as one downloadable document', () => {
    const node = generationLogOverlay(journal(), '/api/v1/media-journals/journal-1/export', () => undefined);

    expect(node.textContent).toContain('provider_request');
    expect(node.textContent).toContain('submitting to local');
    expect(node.textContent).toContain('image · conversation · completed');
    const download = node.querySelector('[data-testid="download-generation-log"]') as HTMLAnchorElement;
    expect(download.getAttribute('href')).toBe('/api/v1/media-journals/journal-1/export');
    expect(download.getAttribute('download')).toBe('generation-journal-journal-1.md');
  });

  it('says plainly that the log is safe to share alongside the image', () => {
    const node = generationLogOverlay(journal(), '/export', () => undefined);
    expect(node.textContent).toContain('Credentials, provider addresses, and server paths are removed');
  });
});
