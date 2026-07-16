import { expect, type Page, test } from '@playwright/test';

const session = { user_id: 'user-1', expires_at: 4_000_000_000, ttl_seconds: 1800, is_admin: true };
const workspace = { id: 'workspace-1', name: 'Main Workspace', created_at: 100 };
const persona = {
  id: 'persona-1',
  workspace_id: workspace.id,
  workspace_ids: [workspace.id],
  name: 'Nova',
  avatar_url: null,
  system_prompt: 'Be thoughtful.',
  personality_details: null,
  traits: {},
  default_model: 'demo',
  preferred_voice: null,
  preferred_tts_model: null,
  preferred_tts_speed: null,
  preferred_voice_openai: null,
  preferred_tts_model_openai: null,
  preferred_tts_speed_openai: null,
  preferred_voice_local: null,
  preferred_tts_model_local: null,
  preferred_tts_speed_local: null,
  created_at: 100,
};
const settings = {
  global_default_model: 'demo',
  default_memory_mode: 'saved',
  stt_provider: 'disabled',
  tts_provider: 'disabled',
  tts_format: 'wav',
  openai_api_key: null,
  onboarding_done: true,
  preferences: {
    general_theme: 'dark',
    general_show_system_messages: false,
    general_show_thinking: false,
    general_auto_logout: true,
    general_voice_responses: true,
    general_show_viz: false,
    image_provider: 'local',
    image_size: '1024x1024',
    image_quality: 'none',
    image_local_backend: 'automatic1111',
    image_local_base_url: '',
    video_provider: 'disabled',
  },
};
const taskProfile = {
  role: 'title_generation',
  title: 'Chat titles',
  description: 'Creates short conversation titles independently from persona behavior.',
  enabled: true,
  provider: 'ollama',
  model: null as string | null,
  fallback_provider: null,
  fallback_model: null,
  max_input_tokens: 512,
  max_output_tokens: 64,
  timeout_seconds: 30,
  temperature: 0.1,
  fallback_policy: 'deterministic',
  updated_at: 100,
};
const baseMediaResource = {
  id: 'media-model-1', resource_type: 'model', kind: 'image', name: 'Fantasy model',
  provider_key: 'local-image', backend: 'automatic1111', external_id: 'fantasy.safetensors',
  enabled: true, priority: 50, operations: ['generate'], domains: ['fantasy'], content_tags: ['general'],
  features: ['text_to_image'], estimated_vram_mb: 6500, estimated_load_seconds: 3,
  default_settings: { steps: 24 }, notes: '', compatible_model_ids: [], revision: 1, created_at: 100, updated_at: 100,
};
const visualIdentityProfile = {
  id: 'identity-1', persona_id: persona.id, status: 'draft', consent_status: 'granted',
  appearance_description: 'Silver hair and blue eyes.', acceptance_threshold: 0.78,
  max_generation_attempts: 2, failure_policy: 'block_claim', revision: 1,
  consent_granted_at: 100, consent_withdrawn_at: null, created_at: 100, updated_at: 100,
  approved_reference_count: 0, generation_workflow_configured: false,
  verification_configured: false, validation_ready: false, references: [],
};

test('login completes the first-run workspace and persona journey', async ({ page }) => {
  let authenticated = false;
  let createdWorkspace = false;
  let createdPersona = false;
  let onboardingDone = false;
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    if (path === '/api/v1/session' && method === 'GET' && !authenticated) {
      await json(route, { error: { code: 'authentication_required', message: 'authentication required' } }, 401);
    } else if (path === '/api/v1/session' && method === 'POST') {
      authenticated = true;
      await json(route, session);
    } else if (path === '/api/v1/models') await json(route, { models: ['demo'] });
    else if (path === '/api/v1/workspaces' && method === 'GET') await json(route, { items: createdWorkspace ? [workspace] : [] });
    else if (path === '/api/v1/workspaces' && method === 'POST') {
      createdWorkspace = true;
      await json(route, workspace);
    } else if (path === '/api/v1/personas' && method === 'GET') await json(route, { items: createdPersona ? [persona] : [] });
    else if (path === '/api/v1/personas' && method === 'POST') {
      createdPersona = true;
      await json(route, persona);
    } else if (path === '/api/v1/chats') await json(route, { items: [] });
    else if (path === '/api/v1/memories') await json(route, { items: [] });
    else if (path === '/api/v1/capability-requests') await json(route, { items: [] });
    else if (path === '/api/v1/settings' && method === 'GET') await json(route, { ...settings, onboarding_done: false });
    else if (path === '/api/v1/settings' && method === 'PUT') {
      onboardingDone = request.postDataJSON().onboarding_done === true;
      await json(route, { ...settings, onboarding_done: true });
    } else await json(route, { error: { code: 404, message: `Unhandled ${method} ${path}` } }, 404);
  });

  await page.goto('/');
  await page.getByTestId('auth-username').fill('owner');
  await page.getByTestId('auth-password').fill('correct horse');
  await page.getByTestId('auth-login').click();
  await expect(page.getByRole('dialog', { name: 'Welcome to Nice Assistant' })).toBeVisible();
  await page.getByRole('dialog').locator('input').fill('Main Workspace');
  await page.getByRole('dialog').getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('dialog', { name: 'Create first persona' })).toBeVisible();
  await page.getByRole('dialog').locator('input').fill('Nova');
  await page.getByRole('dialog').getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('dialog', { name: 'Default personality' })).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByTestId('client-phase')).toHaveText('Idle');
  expect(createdWorkspace).toBe(true);
  expect(createdPersona).toBe(true);
  expect(onboardingDone).toBe(true);
});

test('typed chat streams a turn and persists the canonical result', async ({ page }) => {
  const fixture = await installAuthenticatedFixture(page);
  await page.goto('/#/chats/chat-1');
  await expect(page.getByText('Earlier reply')).toBeVisible();
  await page.getByTestId('chat-input').fill('Hello there');
  await page.getByTestId('chat-send').click();
  await expect(page.getByText('Hello from stream')).toBeVisible();
  await expect(page.getByTestId('client-phase')).toHaveText('Idle');
  expect(fixture.turnBody?.text).toBe('Hello there');
  expect(fixture.turnBody?.model_settings.context_window_tokens).toBe(4096);
  expect(fixture.turnBody).toHaveProperty('workspace_id', workspace.id);
});

test('completed turns refresh the generated chat title in the visible header', async ({ page }) => {
  await installAuthenticatedFixture(page, { renameOnTurn: true });
  await page.goto('/#/chats/chat-1');
  await page.getByTestId('chat-input').fill('Plan a perfect summer morning');
  await page.getByTestId('chat-send').click();

  await expect(page.locator('.header-title')).toHaveText('Perfect Summer Morning');
});

test('active direct media work exposes cancellation and returns cleanly to idle', async ({ page }) => {
  const fixture = await installAuthenticatedFixture(page, { holdMedia: true });
  await page.goto('/#/chats/chat-1');
  await page.getByTitle('Generate an image from this reply').click();
  const cancel = page.getByTestId('chat-cancel');
  await expect(cancel).toBeVisible();
  await expect(cancel).toBeEnabled();
  await cancel.click();
  await expect.poll(() => fixture.mediaCancelled).toBe(true);
  await expect(page.getByTestId('client-phase')).toHaveText('Idle');
  await expect(page.getByTestId('chat-send')).toBeVisible();
  await expect(page.getByText('image generation cancelled', { exact: false })).toHaveCount(0);
});

test('settings review memory and media use only canonical APIs', async ({ page }) => {
  const fixture = await installAuthenticatedFixture(page);
  await page.goto('/#/chats/chat-1');
  await page.getByTestId('open-settings').click();
  await page.getByTestId('settings-nav-memory').click();
  await expect(page.getByText('Pending review (1)')).toBeVisible();
  await page.getByText('Pending review (1)').click();
  await page.getByRole('button', { name: 'Approve' }).click();
  await expect(page.getByText('Active (1)')).toBeVisible();
  await page.getByTestId('settings-nav-general').click();
  await page.locator('.setting-row').filter({ hasText: 'Theme' }).locator('select').selectOption('light');
  await page.getByTestId('settings-save').click();
  await expect(page.getByText('Settings saved')).toBeVisible();
  await page.getByTestId('settings-nav-task-models').click();
  const taskModelCard = page.getByTestId('task-model-title_generation');
  await taskModelCard.locator(':scope > summary').click();
  await taskModelCard.locator('.setting-row').filter({ hasText: 'Primary model' }).locator('select').selectOption('demo');
  const taskModelSaveRequest = page.waitForRequest((request) =>
    request.method() === 'PUT' && new URL(request.url()).pathname === '/api/v1/task-models/title_generation',
  );
  await page.getByTestId('task-model-save-title_generation').click();
  expect((await taskModelSaveRequest).postDataJSON().model).toBe('demo');
  await expect(page.getByText(/Ready: Task model is ready/)).toBeVisible();
  await page.getByTestId('settings-nav-media-catalog').click();
  const resourceCard = page.getByTestId('media-resource-media-model-1');
  await resourceCard.locator(':scope > summary').click();
  await resourceCard.locator('.setting-row').filter({ hasText: 'Name' }).locator('input').first().fill('Fantasy portrait model');
  const mediaResourceSaveRequest = page.waitForRequest((request) =>
    request.method() === 'PUT' && new URL(request.url()).pathname === '/api/v1/media-catalog/resources/media-model-1',
  );
  await page.getByTestId('media-resource-save-media-model-1').click();
  expect((await mediaResourceSaveRequest).postDataJSON().name).toBe('Fantasy portrait model');
  await page.getByTestId('media-plan-preview').click();
  await expect(page.locator('.media-plan-preview')).toContainText('Ready');
  await page.getByRole('button', { name: '✕ Close' }).click();
  await page.getByTitle('Generate an image from this reply').first().click();
  await expect(page.locator('img.msg-inline-image')).toHaveAttribute('src', '/api/v1/media/media-1');
  expect(fixture.memoryApproved).toBe(true);
  expect(fixture.settingsUpdated).toBe(true);
  expect(fixture.taskModelUpdated).toBe(true);
  expect(fixture.mediaCatalogUpdated).toBe(true);
  expect(fixture.requestedPaths.some((path) => path.startsWith('/api/') && !path.startsWith('/api/v1/'))).toBe(false);
});

test('form controls and native dropdown options stay legible in both themes', async ({ page }) => {
  await installAuthenticatedFixture(page);
  await page.goto('/#/settings/General');
  await expect(page.getByRole('heading', { name: 'General' })).toBeVisible();

  expect(await formControlTheme(page)).toEqual({
    colorScheme: 'dark',
    control: { color: 'rgb(231, 242, 255)', backgroundColor: 'rgb(16, 35, 51)' },
    unclassedInput: { color: 'rgb(231, 242, 255)', backgroundColor: 'rgb(16, 35, 51)' },
    option: { color: 'rgb(231, 242, 255)', backgroundColor: 'rgb(16, 35, 51)' },
  });

  await page.locator('.setting-row').filter({ hasText: 'Theme' }).locator('select').selectOption('light');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  expect(await formControlTheme(page)).toEqual({
    colorScheme: 'light',
    control: { color: 'rgb(21, 48, 73)', backgroundColor: 'rgb(247, 251, 255)' },
    unclassedInput: { color: 'rgb(21, 48, 73)', backgroundColor: 'rgb(247, 251, 255)' },
    option: { color: 'rgb(21, 48, 73)', backgroundColor: 'rgb(255, 255, 255)' },
  });
});

test('everyday settings use progressive disclosure and accessible info tips', async ({ page }) => {
  await installAuthenticatedFixture(page);
  await page.goto('/#/settings/General');

  await expect(page.getByText('Choose the everyday experience')).toBeVisible();
  await expect(page.getByTestId('general-advanced-settings')).not.toHaveAttribute('open', '');
  const themeInfo = page.getByRole('button', { name: 'About Theme' });
  const tooltipId = await themeInfo.getAttribute('aria-describedby');
  expect(tooltipId).toBeTruthy();
  await themeInfo.hover();
  await expect(page.locator(`#${tooltipId}`)).toBeVisible();
  await themeInfo.focus();
  await expect(page.locator(`#${tooltipId}`)).toBeVisible();

  await page.getByTestId('settings-nav-image-generation').click();
  await expect(page.getByText('Choose the default image path')).toBeVisible();
  await expect(page.getByText('Local image service', { exact: true })).toBeVisible();
  await expect(page.getByTestId('image-advanced-settings')).not.toHaveAttribute('open', '');

  await page.getByTestId('settings-nav-personas').click();
  await expect(page.getByText('Manage the people you talk with')).toBeVisible();
  await expect(page.locator('details.persona-editor')).not.toHaveAttribute('open', '');
});

test('operator settings lead with readiness and keep expert editors closed', async ({ page }) => {
  await installAuthenticatedFixture(page);
  await page.goto('/#/settings/Models');

  await expect(page.getByText('Set the default conversation behavior')).toBeVisible();
  await expect(page.getByText('1 reported by Ollama')).toBeVisible();
  await expect(page.getByTestId('models-advanced-settings')).not.toHaveAttribute('open', '');
  await expect(page.getByTestId('model-overrides-settings')).not.toHaveAttribute('open', '');

  await page.getByTestId('settings-nav-task-models').click();
  await expect(page.getByText('Configure background intelligence')).toBeVisible();
  const taskModel = page.getByTestId('task-model-title_generation');
  await expect(taskModel).not.toHaveAttribute('open', '');
  await expect(page.getByTestId('settings-save')).toHaveCount(0);
  await taskModel.locator(':scope > summary').click();
  await expect(taskModel).toHaveAttribute('open', '');
  await expect(page.getByTestId('task-model-advanced-title_generation')).not.toHaveAttribute('open', '');

  await page.getByTestId('settings-nav-media-catalog').click();
  await expect(page.getByText('Teach the media coordinator what to use')).toBeVisible();
  const mediaResource = page.getByTestId('media-resource-media-model-1');
  await expect(mediaResource).not.toHaveAttribute('open', '');
  await mediaResource.locator(':scope > summary').click();
  await expect(mediaResource).toHaveAttribute('open', '');
  await expect(page.getByTestId('media-resource-advanced-media-model-1')).not.toHaveAttribute('open', '');
});

test('visual identity guides reference setup without exposing internal media IDs', async ({ page }) => {
  await installAuthenticatedFixture(page);
  await page.goto('/#/settings/Visual%20Identity');
  await expect(page.getByRole('heading', { name: 'Visual Identity' })).toBeVisible();
  await expect(page.getByText('Keep each persona visually recognizable')).toBeVisible();
  await expect(page.getByText('Reference-aware generation', { exact: true })).toBeVisible();
  await expect(page.getByText('ComfyUI needs an identity model plus a bound workflow in Media Catalog', { exact: false })).toBeVisible();
  await expect(page.getByText('Protected media ID')).toHaveCount(0);
  await expect(page.getByTestId('identity-advanced-settings')).not.toHaveAttribute('open', '');

  await page.locator('.identity-attestation input').check();
  await page.getByTestId('identity-reference-gallery-open').click();
  await expect(page.getByTestId('identity-media-picker-reference')).toBeVisible();
  await expect(page.getByAltText('Generated image available for selection')).toHaveAttribute(
    'src',
    '/api/v1/media/media-1',
  );
  await expect(page.getByRole('button', { name: 'Use as reference' })).toBeEnabled();
});

test('model media requests remain pending until the user approves them', async ({ page }) => {
  const chat = {
    id: 'chat-1', workspace_id: workspace.id, persona_id: persona.id, model_override: 'demo', memory_mode: 'saved',
    title: 'Capability chat', hidden_in_ui: false, created_at: 100, updated_at: 100,
  };
  const messages = [
    { id: 'user-1', role: 'user', text: 'Show me a garden', created_at: 100 },
    { id: 'assistant-1', role: 'assistant', text: 'I can create that.', created_at: 101 },
  ];
  let capability = {
    id: 'capability-1', capability_key: 'media.generate_image', status: 'pending_confirmation', permission_mode: 'confirm',
    arguments: { prompt: 'a moonlit garden' }, result: null as Record<string, unknown> | null, error: null,
    chat_id: chat.id, turn_id: 'turn-1', assistant_message_id: 'assistant-1', job_id: null as string | null,
    requested_at: 102,
    decided_at: null as number | null,
    started_at: null as number | null,
    completed_at: null as number | null,
    expires_at: null as number | null,
  };
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    if (path === '/api/v1/session') await json(route, session);
    else if (path === '/api/v1/models') await json(route, { models: ['demo'] });
    else if (path === '/api/v1/workspaces') await json(route, { items: [workspace] });
    else if (path === '/api/v1/personas') await json(route, { items: [persona] });
    else if (path === '/api/v1/chats' && method === 'GET') await json(route, { items: [chat] });
    else if (path === '/api/v1/chats/chat-1') await json(route, { chat, messages });
    else if (path === '/api/v1/settings') await json(route, settings);
    else if (path === '/api/v1/memories') await json(route, { items: [] });
    else if (path === '/api/v1/capability-requests' && method === 'GET') await json(route, { items: [capability] });
    else if (path === '/api/v1/capability-requests/capability-1/approval' && method === 'POST') {
      capability = { ...capability, status: 'queued', job_id: 'media-job', decided_at: 103 };
      await json(route, capability);
    } else if (path === '/api/v1/capability-requests/capability-1' && method === 'GET') {
      capability = {
        ...capability,
        status: 'completed',
        result: { text: 'Ready.\n\n![Generated image](/api/v1/media/media-1)', mediaId: 'media-1' },
        started_at: 104,
        completed_at: 105,
      };
      await json(route, capability);
    } else if (path === '/api/v1/media/media-1') {
      await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64') });
    } else await json(route, { error: { code: 404, message: `Unhandled ${method} ${path}` } }, 404);
  });

  await page.goto('/#/chats/chat-1');
  await expect(page.getByTestId('capability-request')).toContainText('Approval needed');
  await page.getByTestId('approve-capability').click();
  await expect(page.getByTestId('capability-request')).toContainText('Completed');
  await expect(page.locator('.capability-result img')).toHaveAttribute('src', '/api/v1/media/media-1');
});

async function installAuthenticatedFixture(
  page: Page,
  options: { holdMedia?: boolean; renameOnTurn?: boolean } = {},
): Promise<{
  requestedPaths: string[];
  turnBody: CapturedTurnBody | null;
  memoryApproved: boolean;
  settingsUpdated: boolean;
  taskModelUpdated: boolean;
  mediaCatalogUpdated: boolean;
  mediaCancelled: boolean;
}> {
  const result: {
    requestedPaths: string[];
    turnBody: CapturedTurnBody | null;
    memoryApproved: boolean;
    settingsUpdated: boolean;
    taskModelUpdated: boolean;
    mediaCatalogUpdated: boolean;
    mediaCancelled: boolean;
  } = {
    requestedPaths: [],
    turnBody: null,
    memoryApproved: false,
    settingsUpdated: false,
    taskModelUpdated: false,
    mediaCatalogUpdated: false,
    mediaCancelled: false,
  };
  let chat = {
    id: 'chat-1',
    workspace_id: workspace.id,
    persona_id: persona.id,
    model_override: 'demo',
    memory_mode: 'saved',
    title: 'Existing chat',
    hidden_in_ui: false,
    created_at: 100,
    updated_at: 100,
  };
  let messages = [
    { id: 'user-old', role: 'user', text: 'Earlier', created_at: 100 },
    { id: 'assistant-old', role: 'assistant', text: 'Earlier reply', created_at: 101 },
  ];
  let memory = {
    id: 'memory-1',
    scope: 'chat',
    scope_id: chat.id,
    content: 'The user likes rain.',
    status: 'pending',
    confidence: 0.9,
    source_type: 'conversation',
    source_message_id: 'user-old',
    source_turn_id: 'turn-old',
    extractor_provider: 'ollama',
    extractor_model: 'demo',
    extractor_version: 'memory-candidates-v1',
    supersedes_id: null,
    created_at: 102,
    updated_at: 102,
    reviewed_at: null as number | null,
    forgotten_at: null,
    can_undo: false,
  };
  let mediaResource = { ...baseMediaResource };

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    result.requestedPaths.push(path);
    if (path === '/api/v1/session') await json(route, session);
    else if (path === '/api/v1/models') await json(route, { models: ['demo'] });
    else if (path === '/api/v1/workspaces') await json(route, { items: [workspace] });
    else if (path === '/api/v1/personas') await json(route, { items: [persona] });
    else if (path === '/api/v1/chats' && method === 'GET') await json(route, { items: [chat] });
    else if (path === '/api/v1/settings' && method === 'GET') await json(route, settings);
    else if (path === '/api/v1/settings' && method === 'PUT') {
      result.settingsUpdated = request.postDataJSON().preferences.general_theme === 'light';
      await json(route, { ...settings, preferences: request.postDataJSON().preferences });
    } else if (path === '/api/v1/task-models' && method === 'GET') {
      await json(route, { items: [taskProfile] });
    } else if (path === '/api/v1/task-model-runs' && method === 'GET') {
      await json(route, { items: [] });
    } else if (path === '/api/v1/task-models/title_generation' && method === 'PUT') {
      result.taskModelUpdated = request.postDataJSON().model === 'demo';
      taskProfile.model = request.postDataJSON().model;
      await json(route, taskProfile);
    } else if (path === '/api/v1/task-models/title_generation/check' && method === 'POST') {
      await json(route, {
        role: 'title_generation', ready: true, status: 'ready', message: 'Task model is ready.',
        primary_ready: true, fallback_ready: false, effective_model: 'demo', fallback_effective_model: null,
      });
    } else if (path === '/api/v1/media-catalog' && method === 'GET') {
      await json(route, {
        settings: { vram_budget_mb: 10240, max_loras: 4 },
        resources: [mediaResource],
        vocabulary: { operations: ['generate'], domains: ['fantasy'], content_tags: ['general'], features: ['text_to_image'] },
      });
    } else if (path === '/api/v1/media-catalog/resources/media-model-1' && method === 'PUT') {
      result.mediaCatalogUpdated = request.postDataJSON().name === 'Fantasy portrait model';
      mediaResource = { ...mediaResource, ...request.postDataJSON(), revision: mediaResource.revision + 1, updated_at: 101 };
      await json(route, mediaResource);
    } else if (path === '/api/v1/media-catalog/plan-previews' && method === 'POST') {
      await json(route, {
        id: null, source: 'coordinator', status: 'ready', kind: 'image', operation: 'generate',
        requirements: request.postDataJSON(), selected_resources: [mediaResource], estimated_vram_mb: 6500,
        explanation: { summary: 'Selected deterministically.', selected: [], warnings: [], rejected: [] },
        block: null, created_at: null,
      });
    } else if (path === '/api/v1/identity-validation/settings' && method === 'GET') {
      await json(route, { provider: 'disabled', base_url: '', api_key: '', timeout_seconds: 15 });
    } else if (path === '/api/v1/personas/persona-1/visual-identity' && method === 'GET') {
      await json(route, visualIdentityProfile);
    } else if (path === '/api/v1/personas/persona-1/visual-identity/validations' && method === 'GET') {
      await json(route, { items: [] });
    } else if (path === '/api/v1/personas/persona-1/visual-identity/history' && method === 'GET') {
      await json(route, { items: [] });
    } else if (path === '/api/v1/media' && method === 'GET') {
      await json(route, {
        items: [{
          id: 'media-1', chat_id: 'chat-1', kind: 'image', filename: 'generated.png',
          content_url: '/api/v1/media/media-1', created_at: 100,
        }],
      });
    } else if (path === '/api/v1/memories' && method === 'GET') await json(route, { items: [memory] });
    else if (path === '/api/v1/capability-requests' && method === 'GET') await json(route, { items: [] });
    else if (path === '/api/v1/memories/memory-1/approve') {
      result.memoryApproved = true;
      memory = { ...memory, status: 'active', reviewed_at: 103, can_undo: true };
      await json(route, memory);
    } else if (path === '/api/v1/chats/chat-1' && method === 'GET') await json(route, { chat, messages });
    else if (path === '/api/v1/chats/chat-1/turns' && method === 'POST') {
      const turnBody = request.postDataJSON() as CapturedTurnBody;
      result.turnBody = turnBody;
      if (options.renameOnTurn) chat = { ...chat, title: 'Perfect Summer Morning', updated_at: 111 };
      messages = [
        ...messages,
        { id: 'user-new', role: 'user', text: turnBody.text, created_at: 110 },
        { id: 'assistant-new', role: 'assistant', text: 'Hello from stream', created_at: 111 },
      ];
      await json(route, {
        turn: {
          id: 'turn-1', chat_id: chat.id, job_id: 'chat-job', status: 'queued', provider: 'ollama', model: 'demo',
          user_message_id: 'user-new', assistant_message_id: null, accumulated_text: '', error: null,
          created_at: 110, started_at: null, completed_at: null,
        },
        job: job('chat-job', 'queued', null),
      }, 202);
    } else if (path === '/api/v1/turns/turn-1/events') {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: [
          'id: 1\nevent: turn.started\ndata: {"status":"running"}\n\n',
          'id: 2\nevent: assistant.delta\ndata: {"text":"Hello from stream"}\n\n',
          'id: 3\nevent: turn.completed\ndata: {"status":"completed"}\n\n',
        ].join(''),
      });
    } else if (path === '/api/v1/jobs/chat-job') await json(route, job('chat-job', 'completed', { text: 'Hello from stream' }));
    else if (path === '/api/v1/media/image-jobs') await json(route, { job_id: 'media-job', capability_request_id: 'explicit-capability', chat_id: chat.id, status: 'queued' }, 202);
    else if (path === '/api/v1/jobs/media-job' && method === 'DELETE') {
      result.mediaCancelled = true;
      await json(route, job('media-job', 'cancelled', null));
    } else if (path === '/api/v1/jobs/media-job') {
      await json(
        route,
        options.holdMedia
          ? job('media-job', result.mediaCancelled ? 'cancelled' : 'running', null)
          : job('media-job', 'completed', { mediaId: 'media-1', imageUrl: '/api/images/legacy.png' }),
      );
    }
    else if (path === '/api/v1/media/media-1') await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64') });
    else if (path === '/api/v1/diagnostics/client-events') await json(route, { ok: true });
    else await json(route, { error: { code: 404, message: `Unhandled ${method} ${path}` } }, 404);
  });
  return result;
}

async function formControlTheme(page: Page): Promise<{
  colorScheme: string;
  control: { color: string; backgroundColor: string };
  unclassedInput: { color: string; backgroundColor: string };
  option: { color: string; backgroundColor: string };
}> {
  return page.evaluate(() => {
    const control = document.querySelector<HTMLSelectElement>('.settings-detail select');
    const option = control?.querySelector('option');
    if (!control || !option) throw new Error('expected a settings dropdown with an option');
    const unclassedInput = document.createElement('input');
    document.body.append(unclassedInput);
    const controlStyle = getComputedStyle(control);
    const inputStyle = getComputedStyle(unclassedInput);
    const optionStyle = getComputedStyle(option);
    const result = {
      colorScheme: getComputedStyle(document.documentElement).colorScheme,
      control: { color: controlStyle.color, backgroundColor: controlStyle.backgroundColor },
      unclassedInput: { color: inputStyle.color, backgroundColor: inputStyle.backgroundColor },
      option: { color: optionStyle.color, backgroundColor: optionStyle.backgroundColor },
    };
    unclassedInput.remove();
    return result;
  });
}

interface CapturedTurnBody {
  text: string;
  workspace_id: string | null;
  model_settings: { context_window_tokens?: number };
}

function job(id: string, status: 'queued' | 'running' | 'completed' | 'cancelled', result: Record<string, unknown> | null) {
  return {
    id,
    kind: id === 'media-job' ? 'image' : 'chat',
    status,
    chat_id: 'chat-1',
    turn_id: id === 'chat-job' ? 'turn-1' : null,
    capability_request_id: id === 'media-job' ? 'explicit-capability' : null,
    progress: status === 'completed' ? 'Completed' : status === 'cancelled' ? 'Cancelled' : status === 'running' ? 'Running' : 'Queued',
    queue_position: status === 'queued' ? 1 : null,
    result,
    error: '',
    cancel_requested: status === 'cancelled',
    created_at: 100,
    started_at: status === 'running' || status === 'completed' || status === 'cancelled' ? 101 : null,
    completed_at: status === 'completed' || status === 'cancelled' ? 102 : null,
  };
}

async function json(route: Parameters<Parameters<Page['route']>[1]>[0], body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}
