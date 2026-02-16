const app = document.getElementById('app');
const audio = document.getElementById('ttsAudio');

const state = {
  user: null,
  chats: [],
  currentChat: null,
  messages: [],
  personas: [],
  workspaces: [],
  settings: null,
  models: [],
  status: 'Idle',
  showViz: false,
  drawerOpen: window.innerWidth > 900,
  chatSearch: '',
  showSettings: false,
  recording: false,
  recStart: 0,
  recTimer: 0,
  theme: localStorage.getItem('na_theme') || 'dark',
  showJumpBottom: false,
  showSystemMessages: false,
  showThinkingByDefault: false,
  sessionExpiresAt: null,
  sessionTtlSeconds: 1800,
  lastActivityAt: Date.now(),
  sessionTimer: 0,
  authError: '',
  uiError: '',
  settingsError: '',
  settingsSaving: false,
  settingsSavedAt: 0,
  settingsSection: 'General',
  selectedPersonaId: null,
  selectedModel: null,
  selectedMemoryMode: null,
  draftMessage: '',
  thinkingExpanded: {},
  messagePaneScrollTop: 0,
  stickMessagesToBottom: true,
  activeModelSettingsId: '',
  memoryItems: [],
  showNewChatPersonaModal: false,
  newChatPersonaId: null,
  personaSettingsExpanded: {},
  personaAvatarPreview: '',
  voiceResponsesEnabled: true,
  messageAudioById: {},
  currentAudioMessageId: '',
  memorySectionExpanded: {
    active: false,
    pending: false,
    activeGlobal: false,
    activeWorkspace: false,
    activePersona: false,
    pendingWorkspace: false,
    pendingPersona: false,
  },
};

const DEFAULT_PERSONA_AVATAR = "data:image/svg+xml;utf8,%3Csvg%20xmlns%3D%27http%3A//www.w3.org/2000/svg%27%20viewBox%3D%270%200%2096%2096%27%3E%3Cdefs%3E%3ClinearGradient%20id%3D%27g%27%20x1%3D%270%27%20y1%3D%270%27%20x2%3D%271%27%20y2%3D%271%27%3E%3Cstop%20stop-color%3D%27%2342e8ff%27/%3E%3Cstop%20offset%3D%271%27%20stop-color%3D%27%23a470ff%27/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect%20width%3D%2796%27%20height%3D%2796%27%20rx%3D%2720%27%20fill%3D%27%230b1823%27/%3E%3Ccircle%20cx%3D%2748%27%20cy%3D%2736%27%20r%3D%2716%27%20fill%3D%27url%28%23g%29%27/%3E%3Crect%20x%3D%2720%27%20y%3D%2756%27%20width%3D%2756%27%20height%3D%2724%27%20rx%3D%2712%27%20fill%3D%27url%28%23g%29%27/%3E%3C/svg%3E";

const SETTINGS_DEFAULTS = {
  global_default_model: '',
  default_memory_mode: 'auto',
  stt_provider: 'disabled',
  tts_provider: 'disabled',
  tts_format: 'wav',
  openai_api_key: '',
  onboarding_done: 0,
  general_theme: 'dark',
  general_show_system_messages: false,
  general_show_thinking: false,
  general_auto_logout: true,
  tts_voice: 'alloy',
  stt_language: 'auto',
  image_provider: 'disabled',
  image_size: '1024x1024',
  image_quality: 'auto',
  memory_auto_save_user_facts: true,
  user_display_name: '',
  user_timezone: 'local',
  personas_default_system_prompt: 'Be helpful and concise.',
  workspaces_default_workspace_id: '',
  models_temperature: '0.7',
  models_top_p: '1',
  models_num_predict: '512',
  models_presence_penalty: '0',
  models_frequency_penalty: '0',
  model_overrides: {},
};

const IMAGE_QUALITY_ALIASES = {
  standard: 'medium',
  hd: 'high',
};

const IMAGE_QUALITY_VALUES = ['low', 'medium', 'high', 'auto'];

function normalizeImageQuality(value) {
  const normalized = IMAGE_QUALITY_ALIASES[value] || value;
  return IMAGE_QUALITY_VALUES.includes(normalized) ? normalized : SETTINGS_DEFAULTS.image_quality;
}

const SETTINGS_SECTION_KEYS = {
  General: ['general_theme', 'general_show_system_messages', 'general_show_thinking', 'general_auto_logout', 'global_default_model'],
  TTS: ['tts_provider', 'tts_format', 'tts_voice'],
  STT: ['stt_provider', 'stt_language'],
  'Image Generation': ['image_provider', 'image_size', 'image_quality'],
  Memory: ['default_memory_mode', 'memory_auto_save_user_facts'],
  User: ['user_display_name', 'user_timezone'],
  Personas: ['personas_default_system_prompt'],
  Workspaces: ['workspaces_default_workspace_id'],
  Models: ['global_default_model', 'models_temperature', 'models_top_p', 'models_num_predict', 'models_presence_penalty', 'models_frequency_penalty', 'model_overrides'],
};

function normalizeSettings(raw = {}) {
  let extra = {};
  if (raw.preferences_json) {
    try { extra = JSON.parse(raw.preferences_json); } catch { extra = {}; }
  }
  const normalized = { ...SETTINGS_DEFAULTS, ...raw, ...extra };
  if (!normalized.model_overrides || typeof normalized.model_overrides !== 'object') {
    normalized.model_overrides = {};
  }
  normalized.image_quality = normalizeImageQuality(normalized.image_quality);
  return normalized;
}

function settingsPayload(nextSettings) {
  const core = {
    global_default_model: nextSettings.global_default_model,
    default_memory_mode: nextSettings.default_memory_mode,
    stt_provider: nextSettings.stt_provider,
    tts_provider: nextSettings.tts_provider,
    tts_format: nextSettings.tts_format,
    openai_api_key: nextSettings.openai_api_key,
    onboarding_done: Number(Boolean(nextSettings.onboarding_done)),
  };
  const preferences = {
    general_theme: nextSettings.general_theme,
    general_show_system_messages: Boolean(nextSettings.general_show_system_messages),
    general_show_thinking: Boolean(nextSettings.general_show_thinking),
    general_auto_logout: Boolean(nextSettings.general_auto_logout),
    tts_voice: nextSettings.tts_voice,
    stt_language: nextSettings.stt_language,
    image_provider: nextSettings.image_provider,
    image_size: nextSettings.image_size,
    image_quality: normalizeImageQuality(nextSettings.image_quality),
    memory_auto_save_user_facts: Boolean(nextSettings.memory_auto_save_user_facts),
    user_display_name: nextSettings.user_display_name,
    user_timezone: nextSettings.user_timezone,
    personas_default_system_prompt: nextSettings.personas_default_system_prompt,
    workspaces_default_workspace_id: nextSettings.workspaces_default_workspace_id,
    models_temperature: nextSettings.models_temperature,
    models_top_p: nextSettings.models_top_p,
    models_num_predict: nextSettings.models_num_predict,
    models_presence_penalty: nextSettings.models_presence_penalty,
    models_frequency_penalty: nextSettings.models_frequency_penalty,
    model_overrides: nextSettings.model_overrides || {},
  };
  return { ...core, preferences_json: JSON.stringify(preferences) };
}

function modelNickname(modelName) {
  if (!modelName) return '';
  const override = state.settings?.model_overrides?.[modelName] || {};
  return (override.nickname || '').trim() || modelName;
}

function noteActivity() {
  state.lastActivityAt = Date.now();
  armSessionTimer();
}

document.documentElement.setAttribute('data-theme', state.theme);

const VIZ = { N: 120, bandWidth: 2, maxOffset: 150, attack: 0.35, release: 0.1, spring: 0.12, damping: 0.82, ringR: 180 };
let ctx, analyser, source, freq, dots = [];
let recorder, chunks = [];

function el(tag, attrs = {}, children = []) {
  const n = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (k === 'html') n.innerHTML = v;
    else n[k] = v;
  });
  (Array.isArray(children) ? children : [children]).forEach((c) => n.append(c?.nodeType ? c : document.createTextNode(c ?? '')));
  return n;
}

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) }, ...opts });
  const t = await r.text();
  let j = {};
  try { j = JSON.parse(t); } catch {}
  if (!r.ok) throw new Error(j.error || t || r.status);
  return j;
}

const escapeHtml = (s) => s.replace(/[&<>"']/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
function md(text = '') {
  const blocks = [];
  let tmp = text.replace(/```([\s\S]*?)```/g, (_, code) => {
    blocks.push(`<pre><code>${escapeHtml(code.trim())}</code></pre>`);
    return `__CODE_${blocks.length - 1}__`;
  });
  tmp = escapeHtml(tmp)
    .replace(/!\[(.*?)\]\(([^\s)]+)\)/g, '<img src="$2" alt="$1" class="msg-inline-image" loading="lazy" />')
    .replace(/\[(.*?)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/^\s*[-*]\s+(.+)$/gm, '<li>$1</li>');
  tmp = tmp.replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>');
  tmp = tmp.split(/\n{2,}/).map((p) => (p.startsWith('<') ? p : `<p>${p.replace(/\n/g, '<br/>')}</p>`)).join('');
  blocks.forEach((b, i) => { tmp = tmp.replace(`__CODE_${i}__`, b); });
  return tmp;
}

function ensureAudioGraph() {
  if (ctx) return;
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  source = ctx.createMediaElementSource(audio);
  analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);
  analyser.connect(ctx.destination);
  freq = new Uint8Array(analyser.frequencyBinCount);
  const bands = [...Array(analyser.frequencyBinCount).keys()].sort(() => Math.random() - 0.5);
  dots = [...Array(VIZ.N)].map((_, i) => ({ band: bands[i % bands.length], amp: 0, vel: 0 }));
}

function vizCanvas() {
  const c = el('canvas', { id: 'vizCanvas' });
  c.width = innerWidth;
  c.height = innerHeight;
  addEventListener('resize', () => { c.width = innerWidth; c.height = innerHeight; });
  const g = c.getContext('2d');
  (function loop() {
    requestAnimationFrame(loop);
    if (!state.showViz) return;
    g.clearRect(0, 0, c.width, c.height);
    g.fillStyle = 'rgba(4,13,20,.3)';
    g.fillRect(0, 0, c.width, c.height);
    if (!analyser) return;
    analyser.getByteFrequencyData(freq);
    g.globalCompositeOperation = 'lighter';
    const cx = c.width / 2, cy = c.height / 2;
    for (let i = 0; i < dots.length; i++) {
      const d = dots[i], a = (i / dots.length) * Math.PI * 2;
      let raw = 0;
      for (let b = 0; b < VIZ.bandWidth; b++) raw += (freq[(d.band + b) % freq.length] || 0) / 255;
      raw /= VIZ.bandWidth;
      const target = Math.min(VIZ.maxOffset, raw * VIZ.maxOffset);
      const k = target > d.amp ? VIZ.attack : VIZ.release;
      d.vel += (target - d.amp) * k * VIZ.spring;
      d.vel *= VIZ.damping;
      d.amp += d.vel;
      const r = VIZ.ringR + d.amp, x = cx + Math.cos(a) * r, y = cy + Math.sin(a) * r;
      g.fillStyle = 'rgba(95,247,255,.2)'; g.beginPath(); g.arc(x, y, 17, 0, Math.PI * 2); g.fill();
      g.fillStyle = 'rgba(164, 112, 255, .95)'; g.beginPath(); g.arc(x, y, 5.2, 0, Math.PI * 2); g.fill();
    }
    g.globalCompositeOperation = 'source-over';
  })();
  return c;
}

const fmtDate = (ts) => !ts ? '' : new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });

async function refresh() {
  try { state.models = (await api('/api/models')).models || []; } catch { state.models = []; }
  try {
    state.workspaces = (await api('/api/workspaces')).items;
    state.personas = (await api('/api/personas')).items;
    state.chats = (await api('/api/chats')).items;
    state.settings = normalizeSettings((await api('/api/settings')).settings);
    state.memoryItems = (await api('/api/memory/all')).items || [];
    state.showSystemMessages = Boolean(state.settings.general_show_system_messages);
    state.showThinkingByDefault = Boolean(state.settings.general_show_thinking);
    if (state.settings.general_theme && state.settings.general_theme !== state.theme) {
      state.theme = state.settings.general_theme;
      localStorage.setItem('na_theme', state.theme);
      document.documentElement.setAttribute('data-theme', state.theme);
    }
    const sess = await api('/api/session');
    state.sessionExpiresAt = sess.expiresAt || null;
    state.sessionTtlSeconds = Number(sess.ttlSeconds || 1800);
    state.lastActivityAt = Date.now();
    state.user = true;
  } catch {
    state.user = false;
    state.sessionExpiresAt = null;
    if (state.sessionTimer) {
      clearTimeout(state.sessionTimer);
      state.sessionTimer = 0;
    }
  }
  armSessionTimer();
  render();
}

function armSessionTimer() {
  if (state.sessionTimer) clearTimeout(state.sessionTimer);
  if (!state.user) return;
  if (state.settings && state.settings.general_auto_logout === false) return;
  const ttlMs = Math.max(1000, Number(state.sessionTtlSeconds || 1800) * 1000);
  const ms = Math.max(0, (state.lastActivityAt + ttlMs) - Date.now());
  state.sessionTimer = setTimeout(async () => {
    state.user = false;
    state.sessionExpiresAt = null;
    render();
    try { await api('/api/logout', { method: 'POST' }); } catch {}
  }, ms + 50);
}


window.addEventListener('pointerdown', noteActivity, { passive: true });
window.addEventListener('keydown', noteActivity, { passive: true });
window.addEventListener('scroll', noteActivity, { passive: true });

function scrollMessagesToBottom(smooth = true) {
  const pane = document.getElementById('messagesPane');
  if (!pane) return;
  pane.scrollTo({ top: pane.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
}

function modelSettingsFor(modelName) {
  const overrides = state.settings?.model_overrides || {};
  const selected = (modelName && overrides[modelName]) || {};
  return {
    temperature: selected.temperature ?? state.settings?.models_temperature ?? SETTINGS_DEFAULTS.models_temperature,
    top_p: selected.top_p ?? state.settings?.models_top_p ?? SETTINGS_DEFAULTS.models_top_p,
    num_predict: selected.num_predict ?? state.settings?.models_num_predict ?? SETTINGS_DEFAULTS.models_num_predict,
    presence_penalty: selected.presence_penalty ?? state.settings?.models_presence_penalty ?? SETTINGS_DEFAULTS.models_presence_penalty,
    frequency_penalty: selected.frequency_penalty ?? state.settings?.models_frequency_penalty ?? SETTINGS_DEFAULTS.models_frequency_penalty,
  };
}

function setModelSetting(modelName, key, value) {
  const modelId = modelName || state.activeModelSettingsId;
  if (!modelId) return;
  const overrides = { ...(state.settings.model_overrides || {}) };
  const prev = { ...(overrides[modelId] || {}) };
  prev[key] = value;
  overrides[modelId] = prev;
  state.settings.model_overrides = overrides;
}

function restoreMessagePaneScroll() {
  const pane = document.getElementById('messagesPane');
  if (!pane) return;
  if (state.stickMessagesToBottom) {
    pane.scrollTop = pane.scrollHeight;
  } else {
    pane.scrollTop = Math.min(state.messagePaneScrollTop, Math.max(0, pane.scrollHeight - pane.clientHeight));
  }
}

function setUiError(message) {
  state.uiError = message || '';
  render();
}

function syntheticSystemMessage(personaId) {
  const persona = state.personas.find((p) => p.id === personaId);
  if (!persona?.system_prompt) return [];
  return [{ id: '__sys_prompt__', role: 'system', text: `[Persona system prompt]\n${persona.system_prompt}` }];
}

function splitThinking(text = '') {
  const thinkMatch = text.match(/^\s*<think>([\s\S]*?)<\/think>\s*/i);
  if (!thinkMatch) return { thinking: '', visibleText: text };
  const thinking = (thinkMatch[1] || '').trim();
  const visibleText = text.slice(thinkMatch[0].length).trimStart();
  return { thinking, visibleText: visibleText || text };
}

async function openChat(chat) {
  state.currentChat = chat;
  const detail = await api(`/api/chats/${chat.id}`);
  state.currentChat = detail.chat || chat;
  state.selectedPersonaId = detail.chat?.persona_id || chat.persona_id || null;
  state.selectedModel = detail.chat?.model_override || chat.model_override || state.settings?.global_default_model || state.models[0] || null;
  state.selectedMemoryMode = detail.chat?.memory_mode || chat.memory_mode || state.settings?.default_memory_mode || 'auto';
  state.messages = detail.messages;
  state.stickMessagesToBottom = true;
  state.showJumpBottom = false;
  render();
  scrollMessagesToBottom(false);
  if (window.innerWidth < 900) {
    state.drawerOpen = false;
    render();
  }
}

async function hideChat(chatId) {
  await api(`/api/chats/${chatId}`, { method: 'DELETE' });
  if (state.currentChat?.id === chatId) {
    state.currentChat = null;
    state.messages = [];
  }
  await refresh();
}

async function renameChat(chat) {
  const nextTitle = prompt('Rename chat', chat.title || 'New chat');
  if (!nextTitle?.trim()) return;
  await api(`/api/chats/${chat.id}`, { method: 'PUT', body: JSON.stringify({ title: nextTitle.trim() }) });
  await refresh();
}

async function createChatWithPersona(personaId) {
  if (!personaId) {
    setUiError('Pick a persona before starting a new chat.');
    return;
  }
  const c = await api('/api/chats', {
    method: 'POST',
    body: JSON.stringify({
      title: 'New chat',
      personaId,
      memoryMode: state.settings?.default_memory_mode || 'auto',
    }),
  });
  state.newChatPersonaId = null;
  state.showNewChatPersonaModal = false;
  await openChat(c);
  refresh();
}


function extractImageUrl(text = '') {
  const match = text.match(/!\[[^\]]*\]\(([^)]+)\)/);
  return match ? match[1] : '';
}

function speechTextFromReply(text = '') {
  const visible = splitThinking(text).visibleText || '';
  if (!visible.trim()) return '';
  if (/^\s*(Model call failed|Image generation failed|I can generate images, but image generation is currently disabled)/i.test(visible)) return '';
  const withoutImages = visible.replace(/!\[[^\]]*\]\(([^)]+)\)/g, '');
  const withoutUrls = withoutImages.replace(/https?:\/\/\S+/g, '');
  return withoutUrls.trim();
}

function downloadImage(url, suggestedName = 'generated-image.png') {
  const link = document.createElement('a');
  link.href = url;
  link.download = suggestedName;
  link.rel = 'noopener';
  link.click();
}

async function copyTextToClipboard(value) {
  const text = value || '';
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {}
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.append(ta);
  ta.select();
  document.execCommand('copy');
  ta.remove();
}

async function sendChat(text) {
  if (!text?.trim()) return;
  if (!state.currentChat?.id) {
    state.showNewChatPersonaModal = true;
    state.newChatPersonaId = state.newChatPersonaId || state.personas[0]?.id || null;
    setUiError('Start a chat by selecting a persona first.');
    return;
  }
  setUiError('');
  const trimmed = text.trim();
  state.draftMessage = '';
  state.status = 'Thinking';
  const pendingMessage = { id: `__pending__${Date.now()}`, role: 'user', text: trimmed };
  state.messages = [...state.messages, pendingMessage];
  state.stickMessagesToBottom = true;
  state.showJumpBottom = false;
  render();
  scrollMessagesToBottom(false);
  try {
    const personaId = state.selectedPersonaId || state.currentChat?.persona_id || null;
    const model = state.selectedModel || state.currentChat?.model_override || null;
    const memoryMode = state.selectedMemoryMode || state.currentChat?.memory_mode || 'auto';
    const modelSettings = modelSettingsFor(model);
    const r = await api('/api/chat', { method: 'POST', body: JSON.stringify({ text: trimmed, chatId: state.currentChat?.id, personaId, model, memoryMode, modelSettings }) });
    state.currentChat = { ...(state.currentChat || {}), id: r.chatId, persona_id: personaId, model_override: model, memory_mode: memoryMode };
    const detail = await api(`/api/chats/${r.chatId}`);
    state.messages = detail.messages;
    state.selectedPersonaId = detail.chat?.persona_id || state.selectedPersonaId;
    state.selectedModel = detail.chat?.model_override || state.selectedModel;
    state.selectedMemoryMode = detail.chat?.memory_mode || state.selectedMemoryMode;
    state.stickMessagesToBottom = true;
    state.showJumpBottom = false;
    render();
    scrollMessagesToBottom();

    const latestAssistant = [...state.messages].reverse().find((m) => m.role === 'assistant');
    if (state.voiceResponsesEnabled && state.settings?.tts_provider && state.settings.tts_provider !== 'disabled') {
      const spokenText = speechTextFromReply(r.text || '');
      if (spokenText) {
        state.status = 'Speaking';
        render();
        ensureAudioGraph();
        const t = await api('/api/tts', { method: 'POST', body: JSON.stringify({ text: spokenText, chatId: r.chatId, personaId, format: state.settings.tts_format || 'wav' }) });
        audio.src = t.audioUrl;
        if (latestAssistant?.id) {
          state.messageAudioById[latestAssistant.id] = t.audioUrl;
          state.currentAudioMessageId = latestAssistant.id;
        }
        await audio.play();
      } else {
        state.status = 'Idle';
      }
    } else {
      state.status = 'Idle';
    }

    refresh();
  } catch (e) {
    state.messages = state.messages.filter((m) => m.id !== pendingMessage.id);
    state.status = 'Idle';
    setUiError(e.message || 'Failed to send message.');
  }
}
audio.addEventListener('ended', () => { state.status = 'Idle'; state.currentAudioMessageId = ''; render(); });

async function startRec() {
  ensureAudioGraph();
  await ctx.resume();
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
  chunks = [];
  recorder.ondataavailable = (e) => chunks.push(e.data);
  recorder.start();
  state.recording = true;
  state.recStart = Date.now();
  state.status = 'Listening';
  render();
  state.recTimer = setInterval(() => render(), 250);
}

async function stopRec() {
  if (!recorder || recorder.state === 'inactive') return;
  recorder.stop();
  await new Promise((r) => (recorder.onstop = r));
  clearInterval(state.recTimer);
  state.recording = false;
  const blob = new Blob(chunks, { type: 'audio/webm' });
  const fd = new FormData();
  fd.append('file', blob, 'audio.webm');
  state.status = 'Thinking';
  render();
  const r = await fetch('/api/stt', { method: 'POST', body: fd });
  const j = await r.json();
  if (j.text) document.getElementById('chatInput').value = j.text;
  state.status = 'Idle';
  render();
}

function authView() {
  return el('div', { class: 'main-pane glass' }, [
    el('h2', { textContent: 'Nice Assistant Login' }),
    state.authError ? el('div', { class: 'error-banner', textContent: state.authError }) : null,
    el('input', { id: 'u', class: 'search-input', placeholder: 'username' }),
    el('input', { id: 'p', class: 'search-input', placeholder: 'password', type: 'password' }),
    el('div', { class: 'chips' }, [
      el('button', {
        class: 'pill-btn', textContent: 'Create account', onclick: async () => {
          const username = (document.getElementById('u')?.value || '').trim();
          const password = document.getElementById('p')?.value || '';
          state.authError = '';
          render();
          try {
            await api('/api/users', { method: 'POST', body: JSON.stringify({ username, password }) });
            alert('Account created');
          } catch (e) {
            state.authError = e.message || 'Unable to create account.';
            render();
          }
        },
      }),
      el('button', {
        class: 'send-btn', textContent: 'Login', onclick: async () => {
          const username = (document.getElementById('u')?.value || '').trim();
          const password = document.getElementById('p')?.value || '';
          state.authError = '';
          render();
          try {
            await api('/api/login', { method: 'POST', body: JSON.stringify({ username, password }) });
            await refresh();
          } catch (e) {
            state.authError = e.message || 'Wrong username and/or password.';
            render();
          }
        },
      }),
    ]),
  ]);
}

async function ensureWizard() {
  if (!state.user) return;
  if (state.workspaces.length) return;
  if (state.settings?.onboarding_done) return;
  const wsName = prompt('Welcome! Name your first Workspace', 'Main Workspace'); if (!wsName) return;
  const ws = await api('/api/workspaces', { method: 'POST', body: JSON.stringify({ name: wsName }) });
  const pName = prompt('Create your first Persona', 'Assistant');
  const sys = prompt('Optional personality/system prompt', 'Be helpful and concise.');
  await api('/api/personas', { method: 'POST', body: JSON.stringify({ workspaceId: ws.id, name: pName, systemPrompt: sys, defaultModel: state.models[0] || '' }) });
  await api('/api/settings', { method: 'POST', body: JSON.stringify({ global_default_model: state.models[0] || '', default_memory_mode: 'auto', stt_provider: 'disabled', tts_provider: 'disabled', tts_format: 'wav', onboarding_done: 1 }) });
  await refresh();
}

function statusClass() {
  return state.status === 'Listening' ? 'status-listening' : state.status === 'Speaking' ? 'status-speaking' : state.status === 'Thinking' ? 'status-thinking' : 'status-idle';
}

function onMessageScroll(e) {
  const node = e.currentTarget;
  state.messagePaneScrollTop = node.scrollTop;
  state.showJumpBottom = node.scrollTop + node.clientHeight < node.scrollHeight - 130;
  state.stickMessagesToBottom = !state.showJumpBottom;
  const jumpBtn = document.getElementById('jumpBtn');
  if (jumpBtn) jumpBtn.classList.toggle('show', state.showJumpBottom);
}

function managerRow(title, actions = []) {
  return el('div', { class: 'manager-row' }, [el('span', { textContent: title }), el('div', { class: 'chips' }, actions)]);
}


function memoryTargetLabel(mem) {
  if (mem.tier === 'global') return 'All personas';
  if (mem.tier === 'workspace') {
    const w = state.workspaces.find((x) => x.id === mem.tier_ref_id);
    return w ? `Workspace: ${w.name}` : 'Workspace';
  }
  if (mem.tier === 'persona') {
    const p = state.personas.find((x) => x.id === mem.tier_ref_id);
    return p ? `Persona: ${p.name}` : 'Persona';
  }
  if (mem.tier === 'chat') {
    const c = state.chats.find((x) => x.id === mem.tier_ref_id);
    return c ? `Chat: ${c.title || c.id}` : 'Chat';
  }
  return mem.tier;
}

function memoryEditorRow(mem) {
  const content = el('textarea', { class: 'search-input', rows: 2, value: mem.content || '' });
  const tierSel = el('select', { class: 'chip-select' }, [
    el('option', { value: 'global', textContent: 'Global', selected: mem.tier === 'global' }),
    el('option', { value: 'workspace', textContent: 'Workspace', selected: mem.tier === 'workspace' }),
    el('option', { value: 'persona', textContent: 'Persona', selected: mem.tier === 'persona' }),
    el('option', { value: 'chat', textContent: 'Chat', selected: mem.tier === 'chat' }),
  ]);

  const refSel = el('select', { class: 'chip-select' });
  const rebuildRefOptions = () => {
    refSel.innerHTML = '';
    const tier = tierSel.value;
    if (tier === 'global') {
      refSel.append(el('option', { value: '', textContent: 'n/a', selected: true }));
      return;
    }
    const source = tier === 'workspace' ? state.workspaces : (tier === 'persona' ? state.personas : state.chats);
    if (!source.length) {
      refSel.append(el('option', { value: '', textContent: 'No targets' }));
      return;
    }
    source.forEach((item) => {
      const val = item.id;
      const text = item.name || item.title || item.id;
      refSel.append(el('option', { value: val, textContent: text, selected: val === mem.tier_ref_id }));
    });
  };
  rebuildRefOptions();
  tierSel.addEventListener('change', rebuildRefOptions);

  return el('div', { class: 'persona-card' }, [
    el('div', { class: 'meta', textContent: `${fmtDate(mem.created_at)} • ${memoryTargetLabel(mem)}` }),
    content,
    el('div', { class: 'chips' }, [tierSel, refSel]),
    el('div', { class: 'chips' }, [
      el('button', { class: 'send-btn', textContent: 'Save memory', onclick: async () => {
        try {
          await api(`/api/memory/${mem.id}`, { method: 'PUT', body: JSON.stringify({ content: content.value, tier: tierSel.value, tier_ref_id: refSel.value || null }) });
          await refresh();
          state.showSettings = true;
          state.settingsSection = 'Memory';
        } catch (e) { state.settingsError = e.message; render(); }
      } }),
      el('button', { class: 'icon-btn', textContent: 'Delete', onclick: async () => {
        try {
          await api(`/api/memory/${mem.id}`, { method: 'DELETE' });
          await refresh();
          state.showSettings = true;
          state.settingsSection = 'Memory';
        } catch (e) { state.settingsError = e.message; render(); }
      } }),
    ]),
  ]);
}

function toggleMemorySection(key) {
  state.memorySectionExpanded[key] = !state.memorySectionExpanded[key];
  render();
}

function collapsibleHeader(title, key) {
  const open = Boolean(state.memorySectionExpanded[key]);
  return el('button', { class: 'pill-btn', textContent: `${open ? '▾' : '▸'} ${title}`, onclick: () => toggleMemorySection(key) });
}

function groupedMemories() {
  const items = state.memoryItems || [];
  const active = items.filter((m) => m.tier !== 'chat');
  const pending = items.filter((m) => m.tier === 'chat');
  return {
    active,
    pending,
    activeGlobal: active.filter((m) => m.tier === 'global'),
    activeWorkspace: active.filter((m) => m.tier === 'workspace'),
    activePersona: active.filter((m) => m.tier === 'persona'),
    pendingWorkspace: pending.filter((m) => m.tier_ref_id && state.workspaces.some((w) => w.id === m.tier_ref_id)),
    pendingPersona: pending.filter((m) => m.tier_ref_id && state.personas.some((p) => p.id === m.tier_ref_id)),
    pendingChat: pending,
  };
}

function parsePersonaTraits(rawTraits) {
  const defaults = {
    warmth: 50,
    creativity: 50,
    directness: 50,
    conversational: 50,
    casual: 50,
    gender: 'unspecified',
    gender_other: '',
    age: '',
  };
  if (!rawTraits) return defaults;
  if (typeof rawTraits === 'object') {
    return {
      warmth: Number(rawTraits.warmth ?? defaults.warmth),
      creativity: Number(rawTraits.creativity ?? defaults.creativity),
      directness: Number(rawTraits.directness ?? defaults.directness),
      conversational: Number(rawTraits.conversational ?? defaults.conversational),
      casual: Number(rawTraits.casual ?? defaults.casual),
      gender: String(rawTraits.gender || defaults.gender),
      gender_other: String(rawTraits.gender_other || defaults.gender_other),
      age: String(rawTraits.age || defaults.age),
    };
  }
  try {
    const parsed = JSON.parse(rawTraits);
    return {
      warmth: Number(parsed.warmth ?? defaults.warmth),
      creativity: Number(parsed.creativity ?? defaults.creativity),
      directness: Number(parsed.directness ?? defaults.directness),
      conversational: Number(parsed.conversational ?? defaults.conversational),
      casual: Number(parsed.casual ?? defaults.casual),
      gender: String(parsed.gender || defaults.gender),
      gender_other: String(parsed.gender_other || defaults.gender_other),
      age: String(parsed.age || defaults.age),
    };
  } catch {
    return defaults;
  }
}

function personaEditorCard(persona) {
  const traits = parsePersonaTraits(persona.traits_json);
  const personaKey = persona.id || persona.name || String(Math.random());
  if (!(personaKey in state.personaSettingsExpanded)) {
    state.personaSettingsExpanded[personaKey] = false;
  }
  const nameInput = el('input', { class: 'search-input', value: persona.name || '' });
  const avatarInput = el('input', { class: 'search-input', value: persona.avatar_url || '', placeholder: 'Avatar URL (optional)' });
  const fileInput = el('input', { type: 'file', accept: 'image/*', class: 'search-input' });
  fileInput.addEventListener('change', () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => { avatarInput.value = String(reader.result || ''); };
    reader.readAsDataURL(file);
  });
  const personalityInput = el('textarea', { class: 'search-input', rows: 2, value: persona.personality_details || '', placeholder: 'Personality details for this persona' });
  const systemPromptInput = el('textarea', { class: 'search-input', rows: 3, value: persona.system_prompt || '', placeholder: 'System prompt' });
  const modelSelect = el('select', { class: 'chip-select' }, [
    el('option', { value: '', textContent: 'Use app default' }),
    ...state.models.map((m) => el('option', { value: m, textContent: m, selected: m === persona.default_model })),
  ]);
  const ttsVoiceInput = el('input', {
    class: 'search-input',
    value: persona.preferred_voice || '',
    placeholder: 'Persona voice (optional, falls back to Default voice)',
  });
  const genderOptions = ['unspecified', 'male', 'female', 'other'];
  const genderInputs = {};
  const genderOtherInput = el('input', {
    class: 'search-input',
    value: traits.gender_other || '',
    placeholder: 'Enter custom gender',
  });
  const ageInput = el('input', {
    class: 'search-input',
    value: traits.age || '',
    placeholder: 'Age (e.g. 35, young, senior citizen, 55-60)',
  });
  const sliderRow = (label, key) => {
    const value = el('span', { class: 'meta', textContent: String(Math.round(traits[key])) });
    const slider = el('input', {
      type: 'range',
      min: 0,
      max: 100,
      step: 1,
      value: String(Math.round(traits[key])),
      oninput: (e) => { value.textContent = e.target.value; },
    });
    return [label, slider, value];
  };
  const warmthControls = sliderRow('Warmth', 'warmth');
  const creativityControls = sliderRow('Creativity', 'creativity');
  const directnessControls = sliderRow('Directness', 'directness');
  const conversationalControls = sliderRow('Conversational ↔ Informational', 'conversational');
  const casualControls = sliderRow('Casual ↔ Professional', 'casual');

  const setGender = (selected) => {
    genderOptions.forEach((opt) => {
      if (genderInputs[opt]) genderInputs[opt].checked = opt === selected;
    });
    genderOtherInput.style.display = selected === 'other' ? '' : 'none';
  };

  const genderRows = genderOptions.map((opt) => {
    const input = el('input', {
      type: 'checkbox',
      checked: traits.gender === opt,
      onchange: () => setGender(opt),
    });
    genderInputs[opt] = input;
    return el('label', { class: 'checkbox-row' }, [input, opt[0].toUpperCase() + opt.slice(1)]);
  });
  setGender(traits.gender || 'unspecified');

  const body = el('div', { class: 'persona-card-body' }, [
    el('label', { textContent: 'Name' }),
    nameInput,
    el('label', { textContent: 'Avatar URL' }),
    avatarInput,
    el('label', { textContent: 'Upload avatar image' }),
    fileInput,
    el('label', { textContent: 'Personality details' }),
    personalityInput,
    el('label', { textContent: 'System prompt' }),
    systemPromptInput,
    el('label', { textContent: 'Preferred chat model' }),
    modelSelect,
    el('label', { textContent: 'Preferred TTS voice model' }),
    ttsVoiceInput,
    el('label', { textContent: 'Gender' }),
    el('div', { class: 'persona-gender-grid' }, genderRows),
    genderOtherInput,
    el('label', { textContent: 'Age' }),
    ageInput,
    el('label', { textContent: warmthControls[0] }), warmthControls[1], warmthControls[2],
    el('label', { textContent: creativityControls[0] }), creativityControls[1], creativityControls[2],
    el('label', { textContent: directnessControls[0] }), directnessControls[1], directnessControls[2],
    el('label', { textContent: conversationalControls[0] }), conversationalControls[1], conversationalControls[2],
    el('label', { textContent: casualControls[0] }), casualControls[1], casualControls[2],
    el('div', { class: 'chips' }, [
      el('button', { class: 'send-btn', textContent: 'Save persona', onclick: async () => {
        try {
          const selectedGender = genderOptions.find((opt) => genderInputs[opt]?.checked) || 'unspecified';
          await api(`/api/personas/${persona.id}`, {
            method: 'PUT',
            body: JSON.stringify({
              name: nameInput.value.trim() || persona.name,
              system_prompt: systemPromptInput.value,
              default_model: modelSelect.value,
              avatar_url: avatarInput.value,
              personality_details: personalityInput.value,
              preferred_voice: ttsVoiceInput.value.trim(),
              traits: {
                warmth: Number(warmthControls[1].value),
                creativity: Number(creativityControls[1].value),
                directness: Number(directnessControls[1].value),
                conversational: Number(conversationalControls[1].value),
                casual: Number(casualControls[1].value),
                gender: selectedGender,
                gender_other: selectedGender === 'other' ? genderOtherInput.value.trim() : '',
                age: ageInput.value.trim(),
              },
            }),
          });
          await refresh();
          state.settingsSection = 'Personas';
          state.showSettings = true;
        } catch (e) { state.settingsError = e.message; render(); }
      } }),
      el('button', { class: 'icon-btn', textContent: 'Delete', onclick: async () => {
        if (!confirm(`Delete persona "${persona.name}"?`)) return;
        try { await api(`/api/personas/${persona.id}`, { method: 'DELETE' }); await refresh(); state.settingsSection = 'Personas'; state.showSettings = true; }
        catch (e) { state.settingsError = e.message; render(); }
      } }),
    ]),
  ]);

  body.style.display = state.personaSettingsExpanded[personaKey] ? '' : 'none';
  const toggleBtn = el('button', {
    class: 'icon-btn persona-toggle',
    textContent: state.personaSettingsExpanded[personaKey] ? '▾' : '▸',
    onclick: () => {
      state.personaSettingsExpanded[personaKey] = !state.personaSettingsExpanded[personaKey];
      render();
    },
  });

  return el('div', { class: 'persona-card' }, [
    el('div', { class: 'persona-card-header' }, [
      el('img', { class: 'persona-avatar-preview', src: persona.avatar_url || DEFAULT_PERSONA_AVATAR, alt: `${persona.name || 'Persona'} avatar`, onclick: () => { state.personaAvatarPreview = persona.avatar_url || DEFAULT_PERSONA_AVATAR; render(); } }),
      el('strong', { textContent: persona.name || 'Persona' }),
      toggleBtn,
    ]),
    body,
  ]);
}

function settingsPanel() {
  if (!state.showSettings) return null;

  const sectionNames = Object.keys(SETTINGS_SECTION_KEYS);
  if (!sectionNames.includes(state.settingsSection)) state.settingsSection = sectionNames[0];

  const setVal = (key, value) => {
    state.settings[key] = value;
    state.settingsSavedAt = 0;
  };

  const persistSettings = async () => {
    state.settingsSaving = true;
    state.settingsError = '';
    render();
    try {
      await api('/api/settings', { method: 'POST', body: JSON.stringify(settingsPayload(state.settings)) });
      state.settingsSaving = false;
      state.settingsSavedAt = Date.now();
      await refresh();
    } catch (e) {
      state.settingsError = e.message || 'Unable to save settings.';
      state.settingsSaving = false;
      render();
    }
  };

  const resetSection = async (sectionName) => {
    SETTINGS_SECTION_KEYS[sectionName].forEach((k) => { state.settings[k] = SETTINGS_DEFAULTS[k]; });
    if (sectionName === 'General') {
      state.theme = state.settings.general_theme;
      localStorage.setItem('na_theme', state.theme);
      document.documentElement.setAttribute('data-theme', state.theme);
      state.showSystemMessages = Boolean(state.settings.general_show_system_messages);
      state.showThinkingByDefault = Boolean(state.settings.general_show_thinking);
    }
    await persistSettings();
  };

  const workspaceRows = state.workspaces.map((w) => {
    const currentPersonas = state.personas.filter((p) => p.workspace_id === w.id);
    const addSelect = el('select', { class: 'chip-select' }, [
      el('option', { value: '', textContent: 'Add persona to workspace…' }),
      ...state.personas.filter((p) => p.workspace_id !== w.id).map((p) => el('option', { value: p.id, textContent: p.name })),
    ]);
    const addBtn = el('button', { class: 'pill-btn', textContent: 'Add', onclick: async () => {
      const pid = addSelect.value;
      if (!pid) return;
      try { await api(`/api/personas/${pid}`, { method: 'PUT', body: JSON.stringify({ workspace_id: w.id }) }); await refresh(); }
      catch (e) { state.settingsError = e.message; render(); }
    } });
    return el('div', { class: 'persona-card' }, [
      managerRow(w.name, [
        el('button', { class: 'icon-btn', textContent: 'Rename', onclick: async () => {
          const name = prompt('Rename workspace', w.name);
          if (!name?.trim()) return;
          try { await api(`/api/workspaces/${w.id}`, { method: 'PUT', body: JSON.stringify({ name: name.trim() }) }); await refresh(); }
          catch (e) { state.settingsError = e.message; render(); }
        } }),
        el('button', { class: 'icon-btn', textContent: 'Delete', onclick: async () => {
          if (!confirm(`Delete workspace "${w.name}"?`)) return;
          try { await api(`/api/workspaces/${w.id}`, { method: 'DELETE' }); await refresh(); }
          catch (e) { state.settingsError = e.message; render(); }
        } }),
      ]),
      el('div', { class: 'meta', textContent: 'Personas in this workspace' }),
      ...(currentPersonas.length ? currentPersonas.map((p) => managerRow(p.name, [
        el('button', { class: 'icon-btn', textContent: 'Remove', onclick: async () => {
          const fallbackWorkspace = state.workspaces.find((x) => x.id !== w.id);
          if (!fallbackWorkspace) { state.settingsError = 'Create another workspace before removing this persona from the workspace.'; render(); return; }
          try { await api(`/api/personas/${p.id}`, { method: 'PUT', body: JSON.stringify({ workspace_id: fallbackWorkspace.id }) }); await refresh(); }
          catch (e) { state.settingsError = e.message; render(); }
        } }),
      ])) : [el('div', { class: 'meta', textContent: 'No personas assigned yet.' })]),
      el('div', { class: 'chips' }, [addSelect, addBtn]),
    ]);
  });

  const personaRows = state.personas.map((p) => personaEditorCard(p));

  if (!state.activeModelSettingsId) {
    state.activeModelSettingsId = state.settings.global_default_model || state.models[0] || '';
  }
  if (state.activeModelSettingsId && !state.models.includes(state.activeModelSettingsId)) {
    state.activeModelSettingsId = state.settings.global_default_model || state.models[0] || '';
  }
  const activeModelSettings = modelSettingsFor(state.activeModelSettingsId);

  const sectionContent = {
    General: [
      el('label', { textContent: 'Theme' }),
      el('select', { class: 'chip-select', onchange: (e) => {
        setVal('general_theme', e.target.value);
        state.theme = e.target.value;
        localStorage.setItem('na_theme', state.theme);
        document.documentElement.setAttribute('data-theme', state.theme);
      } }, [
        el('option', { value: 'dark', selected: state.settings.general_theme === 'dark', textContent: 'Dark' }),
        el('option', { value: 'light', selected: state.settings.general_theme === 'light', textContent: 'Light' }),
      ]),
      el('label', { textContent: 'Default model' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('global_default_model', e.target.value) },
        [el('option', { value: '', textContent: 'Auto' }), ...state.models.map((m) => el('option', { value: m, textContent: modelNickname(m), selected: m === state.settings.global_default_model }))]),
      el('label', { class: 'checkbox-row' }, [
        el('input', { type: 'checkbox', checked: Boolean(state.settings.general_show_system_messages), onchange: (e) => {
          setVal('general_show_system_messages', e.target.checked);
          state.showSystemMessages = e.target.checked;
        } }),
        'Show system/tool messages by default',
      ]),
      el('label', { class: 'checkbox-row' }, [
        el('input', { type: 'checkbox', checked: Boolean(state.settings.general_show_thinking), onchange: (e) => {
          setVal('general_show_thinking', e.target.checked);
          state.showThinkingByDefault = e.target.checked;
        } }),
        'Show model thinking by default in all chats',
      ]),
      el('label', { class: 'checkbox-row' }, [
        el('input', { type: 'checkbox', checked: state.settings.general_auto_logout !== false, onchange: (e) => {
          setVal('general_auto_logout', e.target.checked);
          noteActivity();
        } }),
        'Auto logout after inactivity',
      ]),
    ],
    TTS: [
      el('label', { textContent: 'Provider' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('tts_provider', e.target.value) }, ['disabled', 'openai', 'local'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.tts_provider }))),
      el('label', { textContent: 'Audio format' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('tts_format', e.target.value) }, ['wav', 'mp3', 'opus'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.tts_format }))),
      el('label', { textContent: 'Default voice model' }),
      el('input', { class: 'search-input', value: state.settings.tts_voice, oninput: (e) => setVal('tts_voice', e.target.value) }),
    ],
    STT: [
      el('label', { textContent: 'Provider' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('stt_provider', e.target.value) }, ['disabled', 'openai', 'local'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.stt_provider }))),
      el('label', { textContent: 'Language' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('stt_language', e.target.value) }, ['auto', 'en', 'es', 'fr', 'de'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.stt_language }))),
    ],
    'Image Generation': [
      el('label', { textContent: 'Provider' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('image_provider', e.target.value) }, ['disabled', 'openai', 'local'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.image_provider }))),
      el('label', { textContent: 'Size' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('image_size', e.target.value) }, ['512x512', '1024x1024', '1536x1024'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.image_size }))),
      el('label', { textContent: 'Quality' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('image_quality', e.target.value) }, IMAGE_QUALITY_VALUES.map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.image_quality }))),
    ],
    Memory: (() => {
      const mem = groupedMemories();
      const content = [
        el('label', { textContent: 'Default memory mode' }),
        el('select', { class: 'chip-select', onchange: (e) => setVal('default_memory_mode', e.target.value) }, ['off', 'manual', 'auto'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.default_memory_mode }))),
        el('label', { class: 'checkbox-row' }, [
          el('input', { type: 'checkbox', checked: Boolean(state.settings.memory_auto_save_user_facts), onchange: (e) => setVal('memory_auto_save_user_facts', e.target.checked) }),
          'Auto-save likely user facts',
        ]),
        el('div', { class: 'meta', textContent: 'Memories are grouped by status and tier.' }),
        el('button', { class: 'pill-btn', textContent: '+ Add global memory', onclick: async () => {
          const contentText = prompt('Memory text');
          if (!contentText?.trim()) return;
          try {
            await api('/api/memory/global', { method: 'POST', body: JSON.stringify({ content: contentText.trim() }) });
            await refresh();
            state.showSettings = true;
            state.settingsSection = 'Memory';
          } catch (e) { state.settingsError = e.message; render(); }
        } }),
        collapsibleHeader('Active Memories', 'active'),
      ];
      if (state.memorySectionExpanded.active) {
        content.push(collapsibleHeader('Global', 'activeGlobal'));
        if (state.memorySectionExpanded.activeGlobal) content.push(...mem.activeGlobal.map((m) => memoryEditorRow(m)));
        content.push(collapsibleHeader('Workspaces', 'activeWorkspace'));
        if (state.memorySectionExpanded.activeWorkspace) content.push(...mem.activeWorkspace.map((m) => memoryEditorRow(m)));
        content.push(collapsibleHeader('Personas', 'activePersona'));
        if (state.memorySectionExpanded.activePersona) content.push(...mem.activePersona.map((m) => memoryEditorRow(m)));
      }
      content.push(collapsibleHeader('Pending', 'pending'));
      if (state.memorySectionExpanded.pending) {
        content.push(collapsibleHeader('Workspaces', 'pendingWorkspace'));
        if (state.memorySectionExpanded.pendingWorkspace) content.push(...mem.pendingWorkspace.map((m) => memoryEditorRow(m)));
        content.push(collapsibleHeader('Personas', 'pendingPersona'));
        if (state.memorySectionExpanded.pendingPersona) content.push(...mem.pendingPersona.map((m) => memoryEditorRow(m)));
        const otherPending = mem.pendingChat.filter((m) => !mem.pendingWorkspace.includes(m) && !mem.pendingPersona.includes(m));
        if (otherPending.length) {
          content.push(el('div', { class: 'meta', textContent: 'Other pending items' }));
          content.push(...otherPending.map((m) => memoryEditorRow(m)));
        }
      }
      return content;
    })(),
    User: [
      el('label', { textContent: 'Display name' }),
      el('input', { class: 'search-input', value: state.settings.user_display_name, oninput: (e) => setVal('user_display_name', e.target.value) }),
      el('label', { textContent: 'Timezone' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('user_timezone', e.target.value) }, ['local', 'UTC', 'America/New_York', 'America/Los_Angeles'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.user_timezone }))),
      el('label', { textContent: 'OpenAI API key' }),
      el('input', { class: 'search-input', placeholder: 'sk-...', value: state.settings.openai_api_key, oninput: (e) => setVal('openai_api_key', e.target.value) }),
    ],
    Personas: [
      el('div', { class: 'persona-card personas-default-prompt' }, [
        el('label', { textContent: 'Default system prompt for new personas' }),
        el('textarea', { class: 'search-input', rows: 3, value: state.settings.personas_default_system_prompt, oninput: (e) => setVal('personas_default_system_prompt', e.target.value) }),
      ]),
      el('div', { class: 'meta', textContent: 'Edit each persona including avatar, detailed personality profile, and trait sliders.' }),
      ...personaRows,
      el('button', { class: 'pill-btn', textContent: '+ Add persona', onclick: async () => {
        const name = prompt('Persona name', 'Assistant');
        if (!name?.trim()) return;
        const workspaceId = state.workspaces[0]?.id;
        if (!workspaceId) { state.settingsError = 'Create a workspace first.'; render(); return; }
        try {
          await api('/api/personas', {
            method: 'POST',
            body: JSON.stringify({
              workspaceId,
              name: name.trim(),
              systemPrompt: state.settings.personas_default_system_prompt,
              personalityDetails: '',
              traits: { warmth: 50, creativity: 50, directness: 50 },
              defaultModel: state.settings.global_default_model || state.models[0] || '',
            }),
          });
          await refresh();
          state.settingsSection = 'Personas';
          state.showSettings = true;
        } catch (e) { state.settingsError = e.message; render(); }
      } }),
    ],
    Workspaces: [
      el('label', { textContent: 'Default workspace' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('workspaces_default_workspace_id', e.target.value) },
        [el('option', { value: '', textContent: 'Auto (first workspace)' }), ...state.workspaces.map((w) => el('option', { value: w.id, textContent: w.name, selected: w.id === state.settings.workspaces_default_workspace_id }))]),
      ...workspaceRows,
      el('button', { class: 'pill-btn', textContent: '+ Add workspace', onclick: async () => {
        const name = prompt('Workspace name', 'New workspace');
        if (!name?.trim()) return;
        try { await api('/api/workspaces', { method: 'POST', body: JSON.stringify({ name: name.trim() }) }); await refresh(); }
        catch (e) { state.settingsError = e.message; render(); }
      } }),
    ],
    Models: [
      el('label', { textContent: 'Default model' }),
      el('select', { class: 'chip-select', onchange: (e) => { setVal('global_default_model', e.target.value); if (!state.activeModelSettingsId) state.activeModelSettingsId = e.target.value; } },
        [el('option', { value: '', textContent: 'Auto' }), ...state.models.map((m) => el('option', { value: m, textContent: modelNickname(m), selected: m === state.settings.global_default_model }))]),
      el('label', { textContent: 'Model-specific tuning' }),
      el('select', { class: 'chip-select', value: state.activeModelSettingsId, onchange: (e) => { state.activeModelSettingsId = e.target.value; render(); } },
        [el('option', { value: '', textContent: state.models.length ? 'Select model…' : 'No models found' }), ...state.models.map((m) => el('option', { value: m, textContent: modelNickname(m), selected: m === state.activeModelSettingsId }))]),
      el('label', { textContent: 'Model nickname' }),
      el('input', { class: 'search-input', disabled: !state.activeModelSettingsId, value: (state.settings.model_overrides?.[state.activeModelSettingsId]?.nickname || state.activeModelSettingsId || ''), oninput: (e) => setModelSetting(state.activeModelSettingsId, 'nickname', e.target.value) }),
      el('label', { textContent: 'Temperature' }),
      el('input', { type: 'number', min: 0, max: 2, step: 0.1, class: 'search-input', disabled: !state.activeModelSettingsId, value: activeModelSettings.temperature, oninput: (e) => setModelSetting(state.activeModelSettingsId, 'temperature', e.target.value) }),
      el('label', { textContent: 'Top P' }),
      el('input', { type: 'number', min: 0, max: 1, step: 0.05, class: 'search-input', disabled: !state.activeModelSettingsId, value: activeModelSettings.top_p, oninput: (e) => setModelSetting(state.activeModelSettingsId, 'top_p', e.target.value) }),
      el('label', { textContent: 'Max output tokens' }),
      el('input', { type: 'number', min: 1, max: 8192, step: 1, class: 'search-input', disabled: !state.activeModelSettingsId, value: activeModelSettings.num_predict, oninput: (e) => setModelSetting(state.activeModelSettingsId, 'num_predict', e.target.value) }),
      el('label', { textContent: 'Presence penalty' }),
      el('input', { type: 'number', min: -2, max: 2, step: 0.1, class: 'search-input', disabled: !state.activeModelSettingsId, value: activeModelSettings.presence_penalty, oninput: (e) => setModelSetting(state.activeModelSettingsId, 'presence_penalty', e.target.value) }),
      el('label', { textContent: 'Frequency penalty' }),
      el('input', { type: 'number', min: -2, max: 2, step: 0.1, class: 'search-input', disabled: !state.activeModelSettingsId, value: activeModelSettings.frequency_penalty, oninput: (e) => setModelSetting(state.activeModelSettingsId, 'frequency_penalty', e.target.value) }),
      el('div', { class: 'meta', textContent: 'Tune each model independently. Values apply when that model is selected for a chat.' }),
    ],
  };

  return el('div', { class: 'settings-screen' }, [
    el('div', { class: 'settings-header' }, [
      el('h2', { textContent: 'Settings' }),
      el('div', { class: 'chips' }, [
        el('button', { class: 'icon-btn', textContent: '✕ Close', onclick: () => { state.showSettings = false; render(); } }),
        el('button', { class: 'send-btn', textContent: state.settingsSaving ? 'Saving…' : 'Save all', disabled: state.settingsSaving, onclick: persistSettings }),
      ]),
    ]),
    state.settingsError ? el('div', { class: 'error-banner', textContent: state.settingsError }) : null,
    state.settingsSavedAt ? el('div', { class: 'success-banner', textContent: `Settings saved at ${new Date(state.settingsSavedAt).toLocaleTimeString()}` }) : null,
    el('div', { class: 'settings-layout' }, [
      el('aside', { class: 'settings-nav glass' }, sectionNames.map((name) => el('button', {
        class: `settings-nav-item ${name === state.settingsSection ? 'active' : ''}`,
        textContent: name,
        onclick: () => { state.settingsSection = name; render(); },
      }))),
      el('section', { class: 'settings-detail glass' }, [
        el('div', { class: 'settings-section-head' }, [
          el('h3', { textContent: state.settingsSection }),
          el('button', { class: 'pill-btn', textContent: 'Reset to Default', onclick: () => resetSection(state.settingsSection) }),
        ]),
        ...sectionContent[state.settingsSection],
      ]),
    ]),
  ]);
}


function messageItem(m, personaId) {
  const isUser = m.role === 'user';
  const hidden = !state.showSystemMessages && (m.role === 'system' || m.role === 'tool');
  if (hidden) return null;
  const { thinking, visibleText } = splitThinking(m.text || '');
  const hasThinking = Boolean(thinking) && m.role === 'assistant';
  const messageId = m.id || `${m.role}-${m.created_at || ''}-${(m.text || '').slice(0, 16)}`;
  const showThinking = state.showThinkingByDefault || Boolean(state.thinkingExpanded[messageId]);
  const personaName = state.personas.find((p) => p.id === (personaId || state.selectedPersonaId))?.name || 'assistant';
  const roleLabel = isUser ? 'You' : (m.role === 'assistant' ? personaName : m.role);
  const imageUrl = extractImageUrl(m.text || '');
  const audioUrl = state.messageAudioById[m.id];
  return el('div', { class: `msg-wrap ${isUser ? 'user' : ''}` }, [
    el('article', { class: `msg ${isUser ? 'user' : 'assistant'}` }, [
      el('small', { textContent: roleLabel }),
      hasThinking && showThinking ? el('details', { class: 'think-block', open: true }, [
        el('summary', { textContent: 'Model thinking' }),
        el('div', { class: 'think-content', html: md(thinking) }),
      ]) : null,
      el('div', { html: md(visibleText || m.text || '') }),
      el('div', { class: 'msg-actions' }, [
        hasThinking ? el('button', {
          class: 'icon-btn',
          textContent: showThinking ? '🙈' : '💭',
          title: showThinking ? 'Hide thinking' : 'Show thinking',
          onclick: () => {
            if (state.showThinkingByDefault) {
              state.showThinkingByDefault = false;
              state.settings.general_show_thinking = false;
            } else {
              state.thinkingExpanded[messageId] = !showThinking;
            }
            render();
          },
        }) : null,
        el('button', { class: 'icon-btn', textContent: '⧉', title: 'Copy', onclick: async () => { try { await copyTextToClipboard(visibleText || m.text || ''); } catch { } } }),
        imageUrl ? el('button', { class: 'icon-btn', textContent: '⬇', title: 'Save image', onclick: () => downloadImage(imageUrl, `nice-assistant-image-${Date.now()}.png`) }) : null,
        audioUrl ? el('button', { class: 'icon-btn', textContent: '⟲', title: 'Replay response audio', onclick: async () => {
          try {
            ensureAudioGraph();
            audio.pause();
            audio.src = audioUrl;
            state.currentAudioMessageId = m.id;
            state.status = 'Speaking';
            render();
            await audio.play();
          } catch {
            state.status = 'Idle';
            render();
          }
        } }) : null,
        state.currentAudioMessageId === m.id ? el('button', { class: 'icon-btn', textContent: '■', title: 'Stop audio', onclick: () => { audio.pause(); audio.currentTime = 0; state.currentAudioMessageId = ''; state.status = 'Idle'; render(); } }) : null,
        el('button', {
          class: 'icon-btn',
          textContent: '🧠',
          title: 'Save to chat memory',
          onclick: async () => {
            const targetChat = state.currentChat?.id;
            if (!targetChat) return;
            await api(`/api/memory/chat/${targetChat}`, { method: 'POST', body: JSON.stringify({ content: m.text || '' }) });
            await refresh();
          },
        }),
      ]),
    ]),
  ]);
}

function render() {
  app.innerHTML = '';
  if (!state.user) {
    app.append(authView());
    return;
  }

  const currentChatTitle = state.currentChat?.title || state.chats.find((c) => c.id === state.currentChat?.id)?.title || 'New conversation';
  const activeWorkspace = state.workspaces.find((w) => w.id === state.currentChat?.workspace_id)?.name || 'Workspace';
  const selectedPersonaId = state.selectedPersonaId || state.currentChat?.persona_id || state.personas[0]?.id;
  const selectedModel = state.selectedModel || state.currentChat?.model_override || state.settings?.global_default_model || state.models[0] || '';
  const selectedMemoryMode = state.selectedMemoryMode || state.currentChat?.memory_mode || state.settings?.default_memory_mode || 'auto';
  const personaName = state.personas.find((p) => p.id === selectedPersonaId)?.name || 'Persona';

  const chatList = state.chats
    .filter((c) => (c.title || '').toLowerCase().includes(state.chatSearch.toLowerCase()))
    .map((c) => el('div', { class: `chat-row ${c.id === state.currentChat?.id ? 'active' : ''}`, onclick: () => openChat(c) }, [
      el('div', { class: 'title', textContent: c.title || 'Untitled chat' }),
      el('div', { class: 'meta', textContent: fmtDate(c.updated_at || c.created_at) }),
      el('div', { class: 'chat-actions' }, [
        el('button', { class: 'icon-btn', textContent: '✎', title: 'Rename chat', onclick: async (e) => { e.stopPropagation(); await renameChat(c); } }),
        el('button', { class: 'icon-btn', textContent: '🗑', title: 'Hide chat', onclick: async (e) => { e.stopPropagation(); await hideChat(c.id); } }),
      ]),
    ]));

  const drawer = el('aside', { class: `drawer glass ${state.drawerOpen ? 'open' : ''}` }, [
    el('div', { class: 'drawer-head' }, [
      el('strong', { textContent: 'Chats' }),
      el('button', { class: 'icon-btn', textContent: '✕', title: 'Hide panel', onclick: () => { state.drawerOpen = false; render(); } }),
    ]),
    el('button', { class: 'send-btn', textContent: '+ New Chat', onclick: async () => {
      state.showNewChatPersonaModal = true;
      state.newChatPersonaId = state.currentChat?.persona_id || state.newChatPersonaId || state.personas[0]?.id || null;
      setUiError('');
      render();
    } }),
    el('input', { class: 'search-input', placeholder: 'Search chats...', value: state.chatSearch, oninput: (e) => { state.chatSearch = e.target.value; render(); } }),
    el('div', { class: 'drawer-list' }, chatList.length ? chatList : [el('div', { class: 'meta', textContent: 'No chats yet.' })]),
  ]);

  const personaId = state.currentChat?.persona_id;
  const messagesForRender = [...(state.showSystemMessages ? syntheticSystemMessage(selectedPersonaId) : []), ...state.messages];

  const composer = el('div', { class: 'composer' }, [
    el('input', {
      id: 'chatInput',
      class: 'composer-input',
      value: state.draftMessage,
      placeholder: 'Ask anything… (Shift+Enter for new line)',
      oninput: (e) => { state.draftMessage = e.target.value; },
      onkeydown: (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendChat(e.currentTarget.value);
        }
      },
    }),
    el('button', { class: 'send-btn', textContent: 'Send', onclick: () => sendChat(state.draftMessage) }),
    el('button', {
      class: `talk-btn ${state.recording ? 'active' : ''}`,
      textContent: state.recording ? `Recording ${Math.floor((Date.now() - state.recStart) / 1000)}s` : 'Hold to Talk',
      onpointerdown: startRec,
      onpointerup: stopRec,
      onpointercancel: stopRec,
      onpointerleave: (e) => { if (e.buttons === 1) stopRec(); },
    }),
  ]);

  const topbar = el('div', { class: 'topbar' }, [
    el('button', { class: 'icon-btn', textContent: '☰', onclick: () => { state.drawerOpen = !state.drawerOpen; render(); } }),
    el('div', { class: 'header-meta' }, [
      el('div', { class: 'header-title', textContent: currentChatTitle }),
      el('div', { class: 'chips' }, [el('button', { class: 'chip', textContent: personaName }), el('button', { class: 'chip', textContent: activeWorkspace }), el('button', { class: 'chip', textContent: modelNickname(state.currentChat?.model_override || state.settings?.global_default_model || 'model') })]),
    ]),
    el('div', { class: `status-pill ${statusClass()}`, textContent: state.status }),
    el('button', { class: 'icon-btn', title: 'Logout', textContent: '⇥', onclick: async () => { await api('/api/logout', { method: 'POST' }); await refresh(); } }),
    el('button', { class: 'icon-btn', textContent: state.showViz ? '◎' : '◉', title: 'Visualizer', onclick: () => { state.showViz = !state.showViz; render(); } }),
    el('button', { class: 'icon-btn', textContent: '⚙', title: 'Settings', onclick: () => { state.showSettings = true; render(); } }),
  ]);

  const main = el('main', { class: 'main-pane glass' }, [
    topbar,
    el('div', { class: 'chips selector-row' }, [
      el('select', {
        id: 'modelSel', class: 'chip-select compact-select', value: selectedModel, onchange: (e) => { state.selectedModel = e.target.value; },
      }, state.models.map((m) => el('option', { value: m, textContent: modelNickname(m), selected: m === selectedModel }))),
      el('select', {
        id: 'memSel', class: 'chip-select compact-select', value: selectedMemoryMode, onchange: (e) => { state.selectedMemoryMode = e.target.value; },
      }, ['off', 'manual', 'auto'].map((m) => el('option', { value: m, textContent: `Memory: ${m}`, selected: m === selectedMemoryMode }))),
      el('button', { class: 'pill-btn', textContent: state.showSystemMessages ? 'Hide system/tool' : 'Show system/tool', onclick: () => { state.showSystemMessages = !state.showSystemMessages; render(); } }),
      el('button', { class: 'pill-btn', textContent: state.showThinkingByDefault ? 'Hide thinking' : 'Show thinking', onclick: () => { state.showThinkingByDefault = !state.showThinkingByDefault; state.settings.general_show_thinking = state.showThinkingByDefault; render(); } }),
      el('button', { class: 'pill-btn', textContent: state.voiceResponsesEnabled ? 'Voice replies: On' : 'Voice replies: Off', onclick: () => { state.voiceResponsesEnabled = !state.voiceResponsesEnabled; render(); } }),
      state.currentAudioMessageId ? el('button', { class: 'pill-btn', textContent: 'Stop audio', onclick: () => { audio.pause(); audio.currentTime = 0; state.currentAudioMessageId = ''; state.status = 'Idle'; render(); } }) : null,
    ]),
    el('section', { id: 'messagesPane', class: 'message-pane glass', onscroll: onMessageScroll },
      messagesForRender.map((m) => messageItem(m, personaId)).filter(Boolean)
    ),
    state.uiError ? el('div', { class: 'error-banner', textContent: state.uiError }) : null,
    el('div', { class: 'record-indicator', textContent: state.recording ? `● Recording… ${Math.floor((Date.now() - state.recStart) / 1000)}s` : 'Ready' }),
    composer,
  ]);

  const scrim = el('div', { class: `scrim ${state.drawerOpen && window.innerWidth < 900 ? 'show' : ''}`, onclick: () => { state.drawerOpen = false; render(); } });
  const jumpBtn = el('button', { id: 'jumpBtn', class: `jump-btn icon-btn ${state.showJumpBottom ? 'show' : ''}`, textContent: '↓ Latest', onclick: () => { state.stickMessagesToBottom = true; state.showJumpBottom = false; scrollMessagesToBottom(); } });
  const viz = el('div', { class: `viz-wrap ${state.showViz ? 'show' : ''}` }, [vizCanvas()]);
  const newChatPersonaModal = state.showNewChatPersonaModal ? el('div', { class: 'modal-backdrop', onclick: (e) => { if (e.target === e.currentTarget) { state.showNewChatPersonaModal = false; render(); } } }, [
    el('div', { class: 'modal-card glass' }, [
      el('h3', { textContent: 'Choose a persona to start this chat' }),
      el('p', { class: 'meta', textContent: 'Each chat is locked to one persona once it starts.' }),
      el('select', {
        class: 'chip-select',
        value: state.newChatPersonaId || state.personas[0]?.id || '',
        onchange: (e) => { state.newChatPersonaId = e.target.value; },
      }, state.personas.map((p) => el('option', { value: p.id, textContent: p.name, selected: p.id === (state.newChatPersonaId || state.personas[0]?.id) }))),
      el('div', { class: 'modal-actions' }, [
        el('button', { class: 'pill-btn', textContent: 'Cancel', onclick: () => { state.showNewChatPersonaModal = false; render(); } }),
        el('button', {
          class: 'send-btn',
          textContent: 'Start chat',
          disabled: !state.personas.length,
          onclick: () => createChatWithPersona(state.newChatPersonaId || state.personas[0]?.id),
        }),
      ]),
    ]),
  ]) : null;
  const avatarPreviewModal = state.personaAvatarPreview ? el('div', {
    class: 'modal-backdrop avatar-preview-backdrop',
    onclick: (e) => { if (e.target === e.currentTarget) { state.personaAvatarPreview = ''; render(); } },
  }, [
    el('div', { class: 'avatar-preview-frame' }, [
      el('button', { class: 'icon-btn avatar-preview-close', textContent: '✕', onclick: () => { state.personaAvatarPreview = ''; render(); } }),
      el('img', { class: 'avatar-preview-full', src: state.personaAvatarPreview, alt: 'Persona avatar preview' }),
    ]),
  ]) : null;
  const shellChildren = state.showSettings ? [settingsPanel(), avatarPreviewModal] : [scrim, drawer, main, jumpBtn, viz, newChatPersonaModal, avatarPreviewModal];
  app.append(el('div', { class: 'app-shell' }, shellChildren));
  requestAnimationFrame(() => {
    restoreMessagePaneScroll();
    const pane = document.getElementById('messagesPane');
    if (pane) onMessageScroll({ currentTarget: pane });
  });
}

refresh().then(ensureWizard);
