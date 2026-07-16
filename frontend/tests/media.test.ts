import { describe, expect, it } from 'vitest';

import type { ApiClient } from '../src/api';
import { extractImageUrl, extractVideoUrl, MediaController, mediaMessage, speechText } from '../src/media';
import { ClientStateMachine, createState } from '../src/state';
import type { AppState, Job } from '../src/types';

function completedJob(result: Job['result']): Job {
  return {
    id: 'job-1',
    kind: 'image',
    status: 'completed',
    chat_id: 'chat-1',
    turn_id: null,
    capability_request_id: null,
    progress: 'Completed',
    queue_position: null,
    result,
    error: '',
    cancel_requested: false,
    created_at: 1,
    started_at: 1,
    completed_at: 2,
  };
}

describe('media presentation', () => {
  it('replaces legacy artifact paths with canonical owner-protected URLs', () => {
    const message = mediaMessage('image', completedJob({ mediaId: 'media-1', imageUrl: '/api/images/legacy.png' }));
    expect(extractImageUrl(message.text)).toBe('/api/v1/media/media-1');
    expect(message.text).not.toContain('/api/images/');
  });

  it('finds video links and produces clean speech text', () => {
    const text = 'Done.\n\n[Download generated video](/api/v1/media/video-1)';
    expect(extractVideoUrl(text)).toBe('/api/v1/media/video-1');
    expect(speechText('**Hello** [world](https://example.test)')).toBe('Hello world');
    expect(speechText('<think>private chain</think>Hi `friend`.\n```ts\nconst secret = true;\n```\n![photo](/media/1) <b>Ready</b>'))
      .toBe('Hi friend. Ready');
  });

  it('treats an acknowledged media cancellation as idle instead of an error', async () => {
    const appState = createState();
    const stateMachine = new ClientStateMachine(appState);
    stateMachine.transition('idle');
    appState.settings = {
      image_provider: 'local',
      image_size: '1024x1024',
      image_quality: 'none',
      image_local_backend: 'comfyui',
      image_local_base_url: 'http://comfyui.test',
    } as AppState['settings'];
    const cancelled = { ...completedJob(null), status: 'cancelled', progress: 'Cancelled' } as Job;
    const client = {
      imageJob: async () => ({ job_id: 'job-1', capability_request_id: 'request-1', chat_id: 'chat-1', status: 'queued' }),
      job: async () => cancelled,
    } as unknown as ApiClient;
    const media = new MediaController(appState, stateMachine, client);

    await expect(media.generateImage('cancel me', 'chat-1')).resolves.toBeNull();
    expect(appState.phase).toBe('idle');
    expect(appState.uiError).toBe('');
    expect(appState.pendingRequest).toBeNull();
  });
});
