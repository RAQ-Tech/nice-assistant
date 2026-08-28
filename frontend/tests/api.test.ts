import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiClient } from '../src/api';

afterEach(() => vi.unstubAllGlobals());

describe('ApiClient', () => {
  it('uses canonical /api/v1 contracts and normalizes safe errors', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ models: ['demo'] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'not_found', message: 'missing' } }), { status: 404, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient();
    await expect(client.models()).resolves.toEqual({ models: ['demo'] });
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/models');
    await expect(client.job('gone')).rejects.toMatchObject({ status: 404, code: 'not_found', message: 'missing' });
  });

  it('strips every server-managed field before saving a catalog resource', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient();

    // A resource exactly as a GET returns it, readonly fields included. The
    // API refuses extras by design, so leaving one in is a 422 on a save
    // that looked valid on screen.
    await client.updateMediaCatalogResource({
      id: 'resource-1',
      revision: 3,
      created_at: 1,
      updated_at: 2,
      needs_binding_review: false,
      source_template_id: 'template-1',
      source_template_version: 2,
      name: 'Model',
      resource_type: 'model',
    } as never);

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    for (const readonly of ['id', 'revision', 'created_at', 'updated_at', 'needs_binding_review', 'source_template_id', 'source_template_version']) {
      expect(body).not.toHaveProperty(readonly);
    }
    expect(body.name).toBe('Model');
  });

  it('adds the CSRF marker to writes but not reads', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient();
    await client.session();
    await client.logout();
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).has('X-Nice-Assistant-CSRF')).toBe(false);
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get('X-Nice-Assistant-CSRF')).toBe('1');
  });

  it('parses fragmented CRLF server-sent events in order', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('id: 1\r'));
        controller.enqueue(encoder.encode('\nevent: turn.started\r\ndata: {"status":"running"}\r\n\r\n'));
        controller.enqueue(encoder.encode('id: 2\nevent: assistant.delta\ndata: {"text":"Hi"}\n\n'));
        controller.close();
      },
    });
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockResolvedValue(new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })));
    const events: string[] = [];
    await new ApiClient().streamTurn('turn-1', (event) => events.push(`${event.id}:${event.event}:${String(event.data.text ?? event.data.status)}`), new AbortController().signal);
    expect(events).toEqual(['1:turn.started:running', '2:assistant.delta:Hi']);
  });

  it('uses canonical task-model profile, readiness, and run routes', async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient();
    await client.taskModels();
    await client.taskModelRuns('memory_extraction', 10);
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/task-models');
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/v1/task-model-runs?limit=10&role=memory_extraction');
  });

  it('uses protected identity endpoints and multipart reference uploads', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ id: 'reference-1' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient();
    const file = new File(['reference'], 'reference.jpg', { type: 'image/jpeg' });
    await client.uploadIdentityReference('persona 1', file, 'user_upload');
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe('/api/v1/personas/persona%201/visual-identity/references');
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.body as FormData).get('attested')).toBe('true');
    expect(new Headers(init?.headers).has('Content-Type')).toBe(false);
  });

  it('puts every writable identity field, because the server defaults whatever it omits', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ id: 'identity-1' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    await new ApiClient().updateVisualIdentity('nova', {
      appearance_description: 'Long pink hair.',
      acceptance_threshold: 0.8,
      max_generation_attempts: 3,
      conditioning_mechanism: 'reference_adapter',
      comparison_retry_enabled: true,
      preferred_preset_ids: ['preset-a', 'preset-b'],
      failure_policy: 'show_unverified',
      conditioning_fallback: 'require_conditioning',
      revision: 7,
    } as never);

    // Omitting any of these turned a save of the appearance text into a silent
    // reset of the mechanism, the retry switch, and the preferred recipes.
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      appearance_description: 'Long pink hair.',
      acceptance_threshold: 0.8,
      max_generation_attempts: 3,
      conditioning_mechanism: 'reference_adapter',
      comparison_retry_enabled: true,
      preferred_preset_ids: ['preset-a', 'preset-b'],
      failure_policy: 'show_unverified',
      conditioning_fallback: 'require_conditioning',
      revision: 7,
    });
  });

  it('lists owner-protected media for visual pickers without exposing filesystem paths', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    await new ApiClient().mediaLibrary('image', 50);
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/media?kind=image&limit=50');
  });

  it('submits typed protected-media image edits', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ job_id: 'job-1' }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    await new ApiClient().imageEditJob({
      prompt: 'change the jacket',
      operation: 'inpaint',
      source_media_id: 'source-1',
      mask_media_id: 'mask-1',
    });
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe('/api/v1/media/image-edit-jobs');
    expect(JSON.parse(String(init?.body))).toMatchObject({ source_media_id: 'source-1', mask_media_id: 'mask-1' });
  });

  it('uses the protected backup verification route', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    await new ApiClient().verifyBackup('snapshot 1.zip');
    const [url, init] = fetchMock.mock.calls[0] ?? [];
    expect(url).toBe('/api/v1/admin/backups/snapshot%201.zip/verify');
    expect(init?.method).toBe('POST');
  });

  it('keeps reversible and permanent chat and memory actions distinct', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const client = new ApiClient();
    await client.hideChat('chat 1');
    await client.deleteChat('chat 1');
    await client.bulkChatAction('hide', ['chat 1', 'chat 2']);
    await client.memoryAction('memory 1', 'forget');
    await client.deleteMemory('memory 1');
    await client.bulkMemoryAction('delete', ['memory 1', 'memory 2']);

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ['/api/v1/chats/chat%201/hide', 'POST'],
      ['/api/v1/chats/chat%201', 'DELETE'],
      ['/api/v1/chats/bulk-actions', 'POST'],
      ['/api/v1/memories/memory%201/forget', 'POST'],
      ['/api/v1/memories/memory%201', 'DELETE'],
      ['/api/v1/memories/bulk-actions', 'POST'],
    ]);
  });
});
