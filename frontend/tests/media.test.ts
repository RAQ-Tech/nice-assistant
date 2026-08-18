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
    const client = {
      imageJob: async () => ({ job_id: 'job-1', capability_request_id: 'request-1', chat_id: 'chat-1', status: 'queued' }),
      capabilityRequest: async () => ({ id: 'request-1', status: 'cancelled' }),
    } as unknown as ApiClient;
    const media = new MediaController(appState, stateMachine, client);

    await expect(media.generateImage('cancel me', 'chat-1')).resolves.toBeNull();
    expect(appState.phase).toBe('idle');
    expect(appState.uiError).toBe('');
    expect(appState.pendingRequest).toBeNull();
  });

  it('leaves the conversation alone while a picture is still generating', async () => {
    const appState = createState();
    const stateMachine = new ClientStateMachine(appState);
    stateMachine.transition('idle');
    appState.currentChat = { id: 'chat-1' } as AppState['currentChat'];
    appState.settings = {
      image_provider: 'local',
      image_size: '1024x1024',
      image_quality: 'none',
      image_local_backend: 'comfyui',
      image_local_base_url: 'http://comfyui.test',
    } as AppState['settings'];

    let polls = 0;
    let chatFetches = 0;
    const client = {
      imageJob: async () => ({ job_id: 'job-1', capability_request_id: 'request-1', chat_id: 'chat-1', status: 'running' }),
      capabilityRequest: async () => {
        polls += 1;
        // Four polls at the same status, then it finishes.
        return { id: 'request-1', status: polls < 5 ? 'running' : 'completed' };
      },
      chat: async () => {
        chatFetches += 1;
        return { chat: { id: 'chat-1' }, messages: [] };
      },
      capabilityRequests: async () => ({ items: [] }),
    } as unknown as ApiClient;
    let renders = 0;
    const media = new MediaController(appState, stateMachine, client);
    media.setChangeHandler(() => { renders += 1; });

    await media.generateImage('a kite', 'chat-1');

    // A request that has not changed looks exactly as it did a third of a
    // second ago. Rebuilding the page for that made it hostile to use while it
    // waited, and asked the server for a conversation nobody had touched.
    expect(polls).toBe(5);
    expect(chatFetches).toBeLessThanOrEqual(3);
    expect(renders).toBeLessThanOrEqual(3);
  });
});
