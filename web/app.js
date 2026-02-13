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
  sessionExpiresAt: null,
  sessionTimer: 0,
  authError: '',
  uiError: '',
  settingsError: '',
  settingsSaving: false,
  settingsSection: 'General',
  selectedPersonaId: null,
  selectedModel: null,
  selectedMemoryMode: null,
  draftMessage: '',
};

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
  tts_voice: 'alloy',
  stt_language: 'auto',
  image_provider: 'disabled',
  image_size: '1024x1024',
  image_quality: 'standard',
  memory_auto_save_user_facts: true,
  user_display_name: '',
  user_timezone: 'local',
  personas_default_system_prompt: 'Be helpful and concise.',
  workspaces_default_workspace_id: '',
  models_temperature: '0.7',
};

const SETTINGS_SECTION_KEYS = {
  General: ['general_theme', 'general_show_system_messages', 'global_default_model'],
  TTS: ['tts_provider', 'tts_format', 'tts_voice'],
  STT: ['stt_provider', 'stt_language'],
  'Image Generation': ['image_provider', 'image_size', 'image_quality'],
  Memory: ['default_memory_mode', 'memory_auto_save_user_facts'],
  User: ['user_display_name', 'user_timezone'],
  Personas: ['personas_default_system_prompt'],
  Workspaces: ['workspaces_default_workspace_id'],
  Models: ['global_default_model', 'models_temperature'],
};

function normalizeSettings(raw = {}) {
  let extra = {};
  if (raw.preferences_json) {
    try { extra = JSON.parse(raw.preferences_json); } catch { extra = {}; }
  }
  return { ...SETTINGS_DEFAULTS, ...raw, ...extra };
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
    tts_voice: nextSettings.tts_voice,
    stt_language: nextSettings.stt_language,
    image_provider: nextSettings.image_provider,
    image_size: nextSettings.image_size,
    image_quality: nextSettings.image_quality,
    memory_auto_save_user_facts: Boolean(nextSettings.memory_auto_save_user_facts),
    user_display_name: nextSettings.user_display_name,
    user_timezone: nextSettings.user_timezone,
    personas_default_system_prompt: nextSettings.personas_default_system_prompt,
    workspaces_default_workspace_id: nextSettings.workspaces_default_workspace_id,
    models_temperature: nextSettings.models_temperature,
  };
  return { ...core, preferences_json: JSON.stringify(preferences) };
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
    state.showSystemMessages = Boolean(state.settings.general_show_system_messages);
    if (state.settings.general_theme && state.settings.general_theme !== state.theme) {
      state.theme = state.settings.general_theme;
      localStorage.setItem('na_theme', state.theme);
      document.documentElement.setAttribute('data-theme', state.theme);
    }
    const sess = await api('/api/session');
    state.sessionExpiresAt = sess.expiresAt || null;
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
  if (!state.user || !state.sessionExpiresAt) return;
  const ms = Math.max(0, (state.sessionExpiresAt * 1000) - Date.now());
  state.sessionTimer = setTimeout(async () => {
    state.user = false;
    state.sessionExpiresAt = null;
    render();
    try { await api('/api/logout', { method: 'POST' }); } catch {}
  }, ms + 50);
}

function scrollMessagesToBottom(smooth = true) {
  const pane = document.getElementById('messagesPane');
  if (!pane) return;
  pane.scrollTo({ top: pane.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
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

async function openChat(chat) {
  state.currentChat = chat;
  const detail = await api(`/api/chats/${chat.id}`);
  state.currentChat = detail.chat || chat;
  state.selectedPersonaId = detail.chat?.persona_id || chat.persona_id || null;
  state.selectedModel = detail.chat?.model_override || chat.model_override || state.settings?.global_default_model || state.models[0] || null;
  state.selectedMemoryMode = detail.chat?.memory_mode || chat.memory_mode || state.settings?.default_memory_mode || 'auto';
  state.messages = detail.messages;
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

async function sendChat(text) {
  if (!text?.trim()) return;
  setUiError('');
  const trimmed = text.trim();
  state.draftMessage = '';
  state.status = 'Thinking';
  const pendingMessage = { id: `__pending__${Date.now()}`, role: 'user', text: trimmed };
  state.messages = [...state.messages, pendingMessage];
  render();
  scrollMessagesToBottom(false);
  try {
    const personaId = state.selectedPersonaId || state.currentChat?.persona_id || null;
    const model = state.selectedModel || state.currentChat?.model_override || null;
    const memoryMode = state.selectedMemoryMode || state.currentChat?.memory_mode || 'auto';
    const r = await api('/api/chat', { method: 'POST', body: JSON.stringify({ text: trimmed, chatId: state.currentChat?.id, personaId, model, memoryMode }) });
    state.currentChat = { ...(state.currentChat || {}), id: r.chatId, persona_id: personaId, model_override: model, memory_mode: memoryMode };
    const detail = await api(`/api/chats/${r.chatId}`);
    state.messages = detail.messages;
    state.selectedPersonaId = detail.chat?.persona_id || state.selectedPersonaId;
    state.selectedModel = detail.chat?.model_override || state.selectedModel;
    state.selectedMemoryMode = detail.chat?.memory_mode || state.selectedMemoryMode;
    render();
    scrollMessagesToBottom();

    if (state.settings?.tts_provider && state.settings.tts_provider !== 'disabled') {
      state.status = 'Speaking';
      render();
      ensureAudioGraph();
      const t = await api('/api/tts', { method: 'POST', body: JSON.stringify({ text: r.text, chatId: r.chatId, personaId, format: state.settings.tts_format || 'wav' }) });
      audio.src = t.audioUrl;
      await audio.play();
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
audio.addEventListener('ended', () => { state.status = 'Idle'; render(); });

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
  state.showJumpBottom = node.scrollTop + node.clientHeight < node.scrollHeight - 130;
  const jumpBtn = document.getElementById('jumpBtn');
  if (jumpBtn) jumpBtn.classList.toggle('show', state.showJumpBottom);
}

function managerRow(title, actions = []) {
  return el('div', { class: 'manager-row' }, [el('span', { textContent: title }), el('div', { class: 'chips' }, actions)]);
}

function settingsPanel() {
  if (!state.showSettings) return null;

  const sectionNames = Object.keys(SETTINGS_SECTION_KEYS);
  if (!sectionNames.includes(state.settingsSection)) state.settingsSection = sectionNames[0];

  const setVal = (key, value) => {
    state.settings[key] = value;
  };

  const persistSettings = async () => {
    state.settingsSaving = true;
    state.settingsError = '';
    render();
    try {
      await api('/api/settings', { method: 'POST', body: JSON.stringify(settingsPayload(state.settings)) });
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
    }
    await persistSettings();
  };

  const workspaceRows = state.workspaces.map((w) => managerRow(w.name, [
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
  ]));

  const personaRows = state.personas.map((p) => managerRow(p.name, [
    el('button', { class: 'icon-btn', textContent: 'Rename', onclick: async () => {
      const name = prompt('Rename persona', p.name);
      if (!name?.trim()) return;
      try { await api(`/api/personas/${p.id}`, { method: 'PUT', body: JSON.stringify({ name: name.trim() }) }); await refresh(); }
      catch (e) { state.settingsError = e.message; render(); }
    } }),
    el('button', { class: 'icon-btn', textContent: 'Delete', onclick: async () => {
      if (!confirm(`Delete persona "${p.name}"?`)) return;
      try { await api(`/api/personas/${p.id}`, { method: 'DELETE' }); await refresh(); }
      catch (e) { state.settingsError = e.message; render(); }
    } }),
  ]));

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
        [el('option', { value: '', textContent: 'Auto' }), ...state.models.map((m) => el('option', { value: m, textContent: m, selected: m === state.settings.global_default_model }))]),
      el('label', { class: 'checkbox-row' }, [
        el('input', { type: 'checkbox', checked: Boolean(state.settings.general_show_system_messages), onchange: (e) => {
          setVal('general_show_system_messages', e.target.checked);
          state.showSystemMessages = e.target.checked;
        } }),
        'Show system/tool messages by default',
      ]),
    ],
    TTS: [
      el('label', { textContent: 'Provider' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('tts_provider', e.target.value) }, ['disabled', 'openai', 'local'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.tts_provider }))),
      el('label', { textContent: 'Audio format' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('tts_format', e.target.value) }, ['wav', 'mp3', 'opus'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.tts_format }))),
      el('label', { textContent: 'Voice' }),
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
      el('select', { class: 'chip-select', onchange: (e) => setVal('image_quality', e.target.value) }, ['standard', 'hd'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.image_quality }))),
    ],
    Memory: [
      el('label', { textContent: 'Default memory mode' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('default_memory_mode', e.target.value) }, ['off', 'manual', 'auto'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.default_memory_mode }))),
      el('label', { class: 'checkbox-row' }, [
        el('input', { type: 'checkbox', checked: Boolean(state.settings.memory_auto_save_user_facts), onchange: (e) => setVal('memory_auto_save_user_facts', e.target.checked) }),
        'Auto-save likely user facts',
      ]),
    ],
    User: [
      el('label', { textContent: 'Display name' }),
      el('input', { class: 'search-input', value: state.settings.user_display_name, oninput: (e) => setVal('user_display_name', e.target.value) }),
      el('label', { textContent: 'Timezone' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('user_timezone', e.target.value) }, ['local', 'UTC', 'America/New_York', 'America/Los_Angeles'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.user_timezone }))),
      el('label', { textContent: 'OpenAI API key' }),
      el('input', { class: 'search-input', placeholder: 'sk-...', value: state.settings.openai_api_key, oninput: (e) => setVal('openai_api_key', e.target.value) }),
    ],
    Personas: [
      el('label', { textContent: 'Default system prompt for new personas' }),
      el('textarea', { class: 'search-input', rows: 3, value: state.settings.personas_default_system_prompt, oninput: (e) => setVal('personas_default_system_prompt', e.target.value) }),
      ...personaRows,
      el('button', { class: 'pill-btn', textContent: '+ Add persona', onclick: async () => {
        const name = prompt('Persona name', 'Assistant');
        if (!name?.trim()) return;
        const workspaceId = state.workspaces[0]?.id;
        if (!workspaceId) { state.settingsError = 'Create a workspace first.'; render(); return; }
        try {
          await api('/api/personas', {
            method: 'POST',
            body: JSON.stringify({ workspaceId, name: name.trim(), systemPrompt: state.settings.personas_default_system_prompt, defaultModel: state.settings.global_default_model || state.models[0] || '' }),
          });
          await refresh();
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
      el('select', { class: 'chip-select', onchange: (e) => setVal('global_default_model', e.target.value) },
        [el('option', { value: '', textContent: 'Auto' }), ...state.models.map((m) => el('option', { value: m, textContent: m, selected: m === state.settings.global_default_model }))]),
      el('label', { textContent: 'Temperature' }),
      el('input', { type: 'number', min: 0, max: 2, step: 0.1, class: 'search-input', value: state.settings.models_temperature, oninput: (e) => setVal('models_temperature', e.target.value) }),
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
  const personaName = state.personas.find((p) => p.id === (personaId || state.selectedPersonaId))?.name || 'assistant';
  const roleLabel = isUser ? 'You' : (m.role === 'assistant' ? personaName : m.role);
  return el('div', { class: `msg-wrap ${isUser ? 'user' : ''}` }, [
    el('article', { class: `msg ${isUser ? 'user' : 'assistant'}` }, [
      el('small', { textContent: roleLabel }),
      el('div', { html: md(m.text || '') }),
      el('div', { class: 'msg-actions' }, [
        el('button', { class: 'icon-btn', textContent: '⧉', title: 'Copy', onclick: () => navigator.clipboard.writeText(m.text || '') }),
        el('button', {
          class: 'icon-btn',
          textContent: '🧠',
          title: 'Save to memory',
          onclick: async () => {
            const targetPersona = document.getElementById('personaSel')?.value || personaId;
            await api(`/api/memory/persona/${targetPersona}`, { method: 'POST', body: JSON.stringify({ content: m.text || '' }) });
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
      const c = await api('/api/chats', { method: 'POST', body: JSON.stringify({ title: 'New chat', memoryMode: state.settings?.default_memory_mode || 'auto' }) });
      await openChat(c);
      refresh();
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
      el('div', { class: 'chips' }, [el('button', { class: 'chip', textContent: personaName }), el('button', { class: 'chip', textContent: activeWorkspace }), el('button', { class: 'chip', textContent: state.currentChat?.model_override || state.settings?.global_default_model || 'model' })]),
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
        id: 'personaSel', class: 'chip-select compact-select', value: selectedPersonaId, onchange: (e) => { state.selectedPersonaId = e.target.value; },
      }, state.personas.map((p) => el('option', { value: p.id, textContent: p.name, selected: p.id === selectedPersonaId }))),
      el('select', {
        id: 'modelSel', class: 'chip-select compact-select', value: selectedModel, onchange: (e) => { state.selectedModel = e.target.value; },
      }, state.models.map((m) => el('option', { value: m, textContent: m, selected: m === selectedModel }))),
      el('select', {
        id: 'memSel', class: 'chip-select compact-select', value: selectedMemoryMode, onchange: (e) => { state.selectedMemoryMode = e.target.value; },
      }, ['off', 'manual', 'auto'].map((m) => el('option', { value: m, textContent: `Memory: ${m}`, selected: m === selectedMemoryMode }))),
      el('button', { class: 'pill-btn', textContent: state.showSystemMessages ? 'Hide system/tool' : 'Show system/tool', onclick: () => { state.showSystemMessages = !state.showSystemMessages; render(); } }),
    ]),
    el('section', { id: 'messagesPane', class: 'message-pane glass', onscroll: onMessageScroll },
      messagesForRender.map((m) => messageItem(m, personaId)).filter(Boolean)
    ),
    state.uiError ? el('div', { class: 'error-banner', textContent: state.uiError }) : null,
    el('div', { class: 'record-indicator', textContent: state.recording ? `● Recording… ${Math.floor((Date.now() - state.recStart) / 1000)}s` : 'Ready' }),
    composer,
  ]);

  const scrim = el('div', { class: `scrim ${state.drawerOpen && window.innerWidth < 900 ? 'show' : ''}`, onclick: () => { state.drawerOpen = false; render(); } });
  const jumpBtn = el('button', { id: 'jumpBtn', class: `jump-btn icon-btn ${state.showJumpBottom ? 'show' : ''}`, textContent: '↓ Latest', onclick: () => scrollMessagesToBottom() });
  const viz = el('div', { class: `viz-wrap ${state.showViz ? 'show' : ''}` }, [vizCanvas()]);
  const shellChildren = state.showSettings ? [settingsPanel()] : [scrim, drawer, main, jumpBtn, viz];
  app.append(el('div', { class: 'app-shell' }, shellChildren));
}

refresh().then(ensureWizard);
