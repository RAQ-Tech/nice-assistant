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
  modal: null,
  modalFocusReturn: null,
  recording: false,
  recStart: 0,
  recTimer: 0,
  recStream: null,
  recMimeType: '',
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
  settingsToastTimer: 0,
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
  chatImagePreview: '',
  voiceResponsesEnabled: true,
  isSending: false,
  isTranscribing: false,
  isSynthesizing: false,
  preferencesSaveTimer: 0,
  messageAudioById: {},
  currentAudioMessageId: '',
  workspaceSectionExpanded: {},
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
  tts_model: 'gpt-4o-mini-tts',
  tts_speed: '1',
  stt_language: 'auto',
  stt_store_recordings: false,
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
  general_voice_responses: true,
  general_show_viz: false,
};

const IMAGE_QUALITY_ALIASES = {
  standard: 'medium',
  hd: 'high',
};

const IMAGE_QUALITY_VALUES = ['low', 'medium', 'high', 'auto'];
const SUPPORTED_IMAGE_SIZES = ['1024x1024', '1024x1536', '1536x1024', 'auto'];
const STT_LANGUAGES = [
  { value: 'auto', label: 'Auto-detect' },
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Español' },
  { value: 'fr', label: 'Français' },
  { value: 'de', label: 'Deutsch' },
];

async function clientLog(type, message, details = {}) {
  if (!state.user) return;
  try {
    await fetch('/api/logs/client', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, message, details }),
    });
  } catch (_e) {
    // no-op: logging must not block UI
  }
}

function logAndShowUiError(message, details = {}) {
  setUiError(message);
  clientLog('ui.error', message, details);
}

function recorderMimeCandidates() {
  return ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/ogg'];
}

function preferredRecorderMimeType() {
  if (!window.MediaRecorder || typeof window.MediaRecorder.isTypeSupported !== 'function') return '';
  return recorderMimeCandidates().find((type) => MediaRecorder.isTypeSupported(type)) || '';
}

function recordingFilenameForMime(mimeType) {
  if (!mimeType) return 'audio.webm';
  if (mimeType.includes('mp4')) return 'audio.mp4';
  if (mimeType.includes('ogg')) return 'audio.ogg';
  return 'audio.webm';
}

function normalizeImageSize(value) {
  return SUPPORTED_IMAGE_SIZES.includes(value) ? value : SETTINGS_DEFAULTS.image_size;
}

function normalizeImageQuality(value) {
  const normalized = IMAGE_QUALITY_ALIASES[value] || value;
  return IMAGE_QUALITY_VALUES.includes(normalized) ? normalized : SETTINGS_DEFAULTS.image_quality;
}

const SETTINGS_SECTION_KEYS = {
  General: ['general_theme', 'general_show_system_messages', 'general_show_thinking', 'general_auto_logout', 'global_default_model'],
  TTS: ['tts_provider', 'tts_format', 'tts_voice', 'tts_model', 'tts_speed'],
  STT: ['stt_provider', 'stt_language', 'stt_store_recordings'],
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
  normalized.image_size = normalizeImageSize(normalized.image_size);
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
    general_voice_responses: Boolean(nextSettings.general_voice_responses),
    general_show_viz: Boolean(nextSettings.general_show_viz),
    tts_voice: nextSettings.tts_voice,
    tts_model: nextSettings.tts_model,
    tts_speed: nextSettings.tts_speed,
    stt_language: nextSettings.stt_language,
    stt_store_recordings: Boolean(nextSettings.stt_store_recordings),
    image_provider: nextSettings.image_provider,
    image_size: normalizeImageSize(nextSettings.image_size),
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

const VIZ = {
  N: 168,
  bandWidth: 2,
  maxOffset: 190,
  attack: 0.38,
  release: 0.11,
  spring: 0.14,
  damping: 0.8,
  ringR: 170,
  pulseR: 84,
  starCount: 140,
};
let ctx, analyser, source, freq, dots = [];
let vizCanvasNode = null;
let vizStars = [];
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
  if (!r.ok) {
    clientLog('api.error', 'api request failed', { path, status: r.status, error: j.error || t || String(r.status) });
    throw new Error(j.error || t || r.status);
  }
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


function bindMessageImagePreview(node) {
  if (!node) return;
  node.querySelectorAll('.msg-inline-image').forEach((img) => {
    if (img.dataset.boundPreview === '1') return;
    img.dataset.boundPreview = '1';
    img.style.cursor = 'zoom-in';
    img.addEventListener('click', () => {
      state.chatImagePreview = img.src;
      render();
    });
  });
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

function buildVizStars() {
  vizStars = [...Array(VIZ.starCount)].map(() => ({
    x: Math.random(),
    y: Math.random(),
    z: 0.15 + Math.random() * 0.85,
    twinkle: Math.random() * Math.PI * 2,
    speed: 0.002 + Math.random() * 0.004,
  }));
}

function vizCanvas() {
  if (vizCanvasNode) return vizCanvasNode;

  const c = el('canvas', { id: 'vizCanvas' });
  const g = c.getContext('2d');

  function resize() {
    const ratio = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
    c.width = Math.floor(innerWidth * ratio);
    c.height = Math.floor(innerHeight * ratio);
    c.style.width = `${innerWidth}px`;
    c.style.height = `${innerHeight}px`;
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  function drawStars(width, height, intensity, time) {
    for (const star of vizStars) {
      star.twinkle += star.speed;
      if (star.twinkle > Math.PI * 2) star.twinkle -= Math.PI * 2;
      const alpha = (0.15 + Math.sin(star.twinkle + time * 0.0012) * 0.12 + intensity * 0.35) * star.z;
      g.fillStyle = `rgba(140, 255, 245, ${Math.max(0.02, alpha)})`;
      g.beginPath();
      g.arc(star.x * width, star.y * height, 0.4 + star.z * 1.4, 0, Math.PI * 2);
      g.fill();
    }
  }

  function drawOrb(cx, cy, radius, intensity, time) {
    const pulse = 1 + Math.sin(time * 0.003) * 0.02 + intensity * 0.16;
    const core = g.createRadialGradient(cx, cy, radius * 0.1, cx, cy, radius * pulse);
    core.addColorStop(0, `rgba(166, 252, 255, ${0.26 + intensity * 0.2})`);
    core.addColorStop(0.45, `rgba(82, 162, 255, ${0.11 + intensity * 0.13})`);
    core.addColorStop(1, 'rgba(26, 56, 118, 0)');
    g.fillStyle = core;
    g.beginPath();
    g.arc(cx, cy, radius * pulse, 0, Math.PI * 2);
    g.fill();
  }

  resize();
  if (!vizStars.length) buildVizStars();
  addEventListener('resize', resize);

  (function loop(time = 0) {
    requestAnimationFrame(loop);
    if (!state.showViz) return;

    const w = innerWidth;
    const h = innerHeight;
    g.clearRect(0, 0, w, h);

    let energy = 0;
    if (analyser) {
      analyser.getByteFrequencyData(freq);
      for (let i = 0; i < freq.length; i++) energy += freq[i] / 255;
      energy /= Math.max(1, freq.length);
    }

    const bg = g.createLinearGradient(0, 0, w, h);
    bg.addColorStop(0, `rgba(2, 8, 20, ${0.78 + energy * 0.1})`);
    bg.addColorStop(0.5, `rgba(5, 16, 38, ${0.56 + energy * 0.12})`);
    bg.addColorStop(1, `rgba(2, 8, 20, ${0.8 + energy * 0.1})`);
    g.fillStyle = bg;
    g.fillRect(0, 0, w, h);

    drawStars(w, h, energy, time);

    const cx = w / 2;
    const cy = h / 2;
    const baseR = Math.min(w, h) * 0.23;
    const ringR = Math.max(VIZ.ringR * 0.58, Math.min(baseR, VIZ.ringR));
    drawOrb(cx, cy, Math.max(VIZ.pulseR, ringR * 0.45), energy, time);

    g.globalCompositeOperation = 'lighter';

    for (let i = 0; i < dots.length; i++) {
      const d = dots[i];
      const a = (i / dots.length) * Math.PI * 2 + time * 0.00012;
      let raw = 0;
      for (let b = 0; b < VIZ.bandWidth; b++) raw += (freq[(d.band + b) % freq.length] || 0) / 255;
      raw /= VIZ.bandWidth;
      const target = Math.min(VIZ.maxOffset, raw * (VIZ.maxOffset + energy * 65));
      const k = target > d.amp ? VIZ.attack : VIZ.release;
      d.vel += (target - d.amp) * k * VIZ.spring;
      d.vel *= VIZ.damping;
      d.amp += d.vel;

      const drift = Math.sin(time * 0.0015 + i * 0.3) * 9;
      const r = ringR + d.amp + drift;
      const x = cx + Math.cos(a) * r;
      const y = cy + Math.sin(a) * r;
      const glow = 7 + raw * 17;
      const p = 1.8 + raw * 4.9;

      g.fillStyle = `rgba(95, 247, 255, ${0.1 + raw * 0.5})`;
      g.beginPath();
      g.arc(x, y, glow, 0, Math.PI * 2);
      g.fill();

      g.fillStyle = `rgba(180, 132, 255, ${0.45 + raw * 0.45})`;
      g.beginPath();
      g.arc(x, y, p, 0, Math.PI * 2);
      g.fill();
    }

    const ringA = 0.12 + energy * 0.35;
    g.strokeStyle = `rgba(114, 248, 255, ${ringA})`;
    g.lineWidth = 1.4;
    g.beginPath();
    g.arc(cx, cy, ringR * (1.05 + Math.sin(time * 0.0024) * 0.012), 0, Math.PI * 2);
    g.stroke();

    g.globalCompositeOperation = 'source-over';
  })();

  vizCanvasNode = c;
  return vizCanvasNode;
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
    state.voiceResponsesEnabled = Boolean(state.settings.general_voice_responses);
    state.showViz = Boolean(state.settings.general_show_viz);
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

function setVizVisible(next, shouldRender = true) {
  state.showViz = Boolean(next);
  if (state.settings) state.settings.general_show_viz = state.showViz;
  schedulePreferenceSave();
  if (shouldRender) render();
}

function setVoiceResponsesEnabled(next, shouldRender = true) {
  state.voiceResponsesEnabled = Boolean(next);
  if (state.settings) state.settings.general_voice_responses = state.voiceResponsesEnabled;
  schedulePreferenceSave();
  if (shouldRender) render();
}

function setShowSystemMessages(next, shouldRender = true) {
  state.showSystemMessages = Boolean(next);
  if (state.settings) state.settings.general_show_system_messages = state.showSystemMessages;
  schedulePreferenceSave();
  if (shouldRender) render();
}

function setShowThinkingByDefault(next, shouldRender = true) {
  state.showThinkingByDefault = Boolean(next);
  if (state.settings) state.settings.general_show_thinking = state.showThinkingByDefault;
  schedulePreferenceSave();
  if (shouldRender) render();
}

function schedulePreferenceSave() {
  if (!state.settings || state.settingsSaving) return;
  if (state.preferencesSaveTimer) clearTimeout(state.preferencesSaveTimer);
  state.preferencesSaveTimer = setTimeout(async () => {
    state.preferencesSaveTimer = 0;
    try {
      await api('/api/settings', { method: 'POST', body: JSON.stringify(settingsPayload(state.settings)) });
      state.settingsSavedAt = Date.now();
      if (state.settingsToastTimer) clearTimeout(state.settingsToastTimer);
      state.settingsToastTimer = setTimeout(() => { state.settingsSavedAt = 0; state.settingsToastTimer = 0; render(); }, 2200);
    } catch (e) {
      state.settingsError = e.message || 'Unable to save preferences.';
    }
    render();
  }, 350);
}

function openModal(config) {
  state.modalFocusReturn = document.activeElement;
  state.modal = { ...config };
  render();
}

function closeModal() {
  state.modal = null;
  const returnTo = state.modalFocusReturn;
  state.modalFocusReturn = null;
  render();
  if (returnTo && typeof returnTo.focus === 'function') returnTo.focus();
}

function runModalAction(action) {
  if (!action || action.disabled) return;
  action.onClick?.();
}

function promptModal({ title, message = '', initialValue = '', placeholder = '', confirmText = 'Save' }) {
  return new Promise((resolve) => {
    openModal({
      title,
      message,
      kind: 'prompt',
      inputValue: initialValue,
      inputPlaceholder: placeholder,
      cancelText: 'Cancel',
      actions: [
        { label: 'Cancel', className: 'pill-btn', onClick: () => { closeModal(); resolve(''); } },
        { label: confirmText, className: 'send-btn', onClick: () => { const v = state.modal?.inputValue || ''; closeModal(); resolve(v); } },
      ],
    });
  });
}

function confirmModal({ title, message, confirmText = 'Delete' }) {
  return new Promise((resolve) => {
    openModal({
      title,
      message,
      cancelText: 'Cancel',
      actions: [
        { label: 'Cancel', className: 'pill-btn', onClick: () => { closeModal(); resolve(false); } },
        { label: confirmText, className: 'send-btn', onClick: () => { closeModal(); resolve(true); } },
      ],
    });
  });
}

async function runOnboardingWizard() {
  const workspaceName = await promptModal({ title: 'Welcome to Nice Assistant', message: 'Name your first workspace.', initialValue: 'Main Workspace', confirmText: 'Continue' });
  if (!workspaceName.trim()) return;
  const personaName = await promptModal({ title: 'Create first persona', message: 'Give your assistant a persona name.', initialValue: 'Assistant', confirmText: 'Continue' });
  if (!personaName.trim()) return;
  const systemPrompt = await promptModal({ title: 'Default personality', message: 'Optional system prompt.', initialValue: 'Be helpful and concise.', confirmText: 'Finish' });
  const ws = await api('/api/workspaces', { method: 'POST', body: JSON.stringify({ name: workspaceName.trim() }) });
  await api('/api/personas', { method: 'POST', body: JSON.stringify({ workspaceId: ws.id, name: personaName.trim(), systemPrompt: systemPrompt.trim(), defaultModel: state.models[0] || '' }) });
  state.settings = state.settings || normalizeSettings();
  state.settings.onboarding_done = 1;
  await api('/api/settings', { method: 'POST', body: JSON.stringify(settingsPayload(state.settings)) });
  await refresh();
}

function activeOverlayVisible() {
  return Boolean(state.modal || state.showNewChatPersonaModal || state.personaAvatarPreview || state.chatImagePreview || state.showSettings);
}

function handleGlobalEscape(e) {
  if (e.key !== 'Escape') return;
  if (state.modal) { closeModal(); return; }
  if (state.showNewChatPersonaModal) { state.showNewChatPersonaModal = false; render(); return; }
  if (state.chatImagePreview) { state.chatImagePreview = ''; render(); return; }
  if (state.personaAvatarPreview) { state.personaAvatarPreview = ''; render(); return; }
  if (state.showSettings) { state.showSettings = false; render(); }
}

document.addEventListener('keydown', handleGlobalEscape);

document.addEventListener('keydown', (e) => {
  if (e.key !== 'Tab' || !state.modal) return;
  const modal = app.querySelector('.modal-card');
  if (!modal) return;
  const focusable = [...modal.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')].filter((n) => !n.disabled);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
});

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
  state.drawerOpen = false;
  render();
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
  const nextTitle = await promptModal({ title: 'Rename chat', initialValue: chat.title || 'New chat', confirmText: 'Rename' });
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
  if (/^\s*(Model call failed|Image generation failed|I can generate images, but image generation is currently disabled|I couldn't generate that image|That image size is not supported by OpenAI|OpenAI couldn't generate that image)/i.test(visible)) return '';
  const withoutImages = visible.replace(/!\[[^\]]*\]\(([^)]+)\)/g, '');
  const withoutUrls = withoutImages.replace(/https?:\/\/\S+/g, '');
  return withoutUrls.trim();
}

function normalizePromptSourceText(text = '') {
  const visible = splitThinking(text).visibleText || text || '';
  return visible
    .replace(/!\[[^\]]*\]\(([^)]+)\)/g, '')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/[`*_>#-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function compactPromptSnippet(text = '', maxLen = 220) {
  if (!text) return '';
  if (text.length <= maxLen) return text;
  return `${text.slice(0, Math.max(0, maxLen - 1)).trim()}…`;
}

function inferVisualStyleHint(sourceText = '') {
  const lowered = sourceText.toLowerCase();
  if (/(logo|brand|wordmark|icon)/.test(lowered)) return 'clean vector logo style';
  if (/(anime|manga|cel shade|studio ghibli)/.test(lowered)) return 'anime illustration style';
  if (/(photo|photoreal|realistic|camera|dslr|portrait)/.test(lowered)) return 'cinematic photorealistic style';
  if (/(pixel|8-bit|retro game)/.test(lowered)) return 'retro pixel art style';
  if (/(watercolor|oil painting|painting|sketch)/.test(lowered)) return 'hand-painted illustration style';
  if (/(futuristic|cyberpunk|sci-fi|neon)/.test(lowered)) return 'cinematic sci-fi concept art style';
  return 'high-detail digital illustration style';
}

function contextualImagePromptFromMessage(message) {
  const assistantText = compactPromptSnippet(normalizePromptSourceText(message?.text || ''), 260);
  if (!assistantText) return 'A polished, coherent scene inspired by the current conversation.';

  const messageIdx = state.messages.findIndex((m) => m.id && message?.id && m.id === message.id);
  const boundedIdx = messageIdx >= 0 ? messageIdx : state.messages.length - 1;
  const recent = state.messages
    .slice(Math.max(0, boundedIdx - 6), boundedIdx + 1)
    .filter((m) => m.role === 'user' || m.role === 'assistant');
  const recentContext = recent
    .map((m) => `${m.role === 'user' ? 'User' : 'Assistant'}: ${compactPromptSnippet(normalizePromptSourceText(m.text || ''), 160)}`)
    .filter((line) => line.split(':')[1]?.trim())
    .join(' | ');
  const latestUser = [...recent].reverse().find((m) => m.role === 'user');
  const userIntent = compactPromptSnippet(normalizePromptSourceText(latestUser?.text || ''), 180);
  const styleHint = inferVisualStyleHint(`${assistantText} ${recentContext}`);

  return [
    `${styleHint}, single cohesive composition, no text overlay, safe for work.`,
    `Primary scene: ${assistantText}`,
    userIntent ? `Conversation intent to preserve: ${userIntent}` : '',
    recentContext ? `Context clues: ${recentContext}` : '',
  ].filter(Boolean).join('\n');
}

async function generateImageFromAssistantMessage(message) {
  if (!state.currentChat?.id) return;
  if (!message || message.role !== 'assistant') return;
  const initialPrompt = contextualImagePromptFromMessage(message);
  const prompt = await promptModal({
    title: 'Generate image from this reply',
    message: 'We drafted a fresh image prompt from this message and recent context. Edit as needed before generating.',
    initialValue: initialPrompt,
    confirmText: 'Generate image',
  });
  if (!prompt?.trim()) return;
  try {
    setUiError('');
    state.status = 'Thinking';
    render();
    await api('/api/images/generate', { method: 'POST', body: JSON.stringify({ prompt: prompt.trim(), chatId: state.currentChat.id }) });
    const withImage = await api(`/api/chats/${state.currentChat.id}`);
    state.messages = withImage.messages;
    state.status = 'Idle';
    state.stickMessagesToBottom = true;
    state.showJumpBottom = false;
    render();
    scrollMessagesToBottom();
  } catch (e) {
    state.status = 'Idle';
    setUiError(e?.message || 'Image generation failed.');
    render();
  }
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

function autoResizeComposer(target) {
  if (!target) return;
  target.style.height = 'auto';
  target.style.height = `${Math.min(target.scrollHeight, 180)}px`;
}

async function sendChat(text) {
  if (state.isSending) return;
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
  state.isSending = true;
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
    if (r.imageOffer?.prompt) {
      const accepted = await confirmModal({ title: 'Receive image?', message: 'Your assistant wants to send an image for this reply.', confirmText: 'Receive image' });
      if (accepted) {
        try {
          await api('/api/images/generate', { method: 'POST', body: JSON.stringify({ prompt: r.imageOffer.prompt, chatId: r.chatId }) });
          const withImage = await api(`/api/chats/${r.chatId}`);
          state.messages = withImage.messages;
        } catch (imageErr) {
          setUiError(imageErr?.message || 'Image generation failed.');
        }
      }
    }
    state.stickMessagesToBottom = true;
    state.showJumpBottom = false;
    render();
    scrollMessagesToBottom();

    const latestAssistant = [...state.messages].reverse().find((m) => m.role === 'assistant');
    if (state.voiceResponsesEnabled && state.settings?.tts_provider && state.settings.tts_provider !== 'disabled') {
      const spokenText = speechTextFromReply(r.text || '');
      if (spokenText) {
        state.status = 'Speaking';
        state.isSynthesizing = true;
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
  } finally {
    state.isSending = false;
    state.isSynthesizing = false;
    render();
  }
}
audio.addEventListener('ended', () => { state.status = 'Idle'; state.currentAudioMessageId = ''; state.isSynthesizing = false; render(); });

async function startRec() {
  if (state.recording || state.isSending || state.isSynthesizing) return;
  ensureAudioGraph();
  await ctx.resume();
  if (!navigator.mediaDevices?.getUserMedia) {
    logAndShowUiError('Microphone is not supported in this browser.', { feature: 'getUserMedia' });
    return;
  }
  if (!window.MediaRecorder) {
    logAndShowUiError('Audio recording is not supported in this browser.', { feature: 'MediaRecorder' });
    return;
  }
  try {
    state.recStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    const message = e?.name === 'NotAllowedError'
      ? 'Microphone permission is blocked. Please allow microphone access for this site.'
      : e?.name === 'NotFoundError'
        ? 'No microphone was found on this device.'
        : e?.name === 'NotReadableError'
          ? 'Microphone is busy in another app. Close other apps and try again.'
          : 'Could not access microphone. Check browser and OS microphone settings.';
    logAndShowUiError(message, { error: e?.message || String(e), code: e?.name || 'unknown' });
    return;
  }

  try {
    const mimeType = preferredRecorderMimeType();
    recorder = mimeType ? new MediaRecorder(state.recStream, { mimeType }) : new MediaRecorder(state.recStream);
    state.recMimeType = mimeType || recorder.mimeType || 'audio/webm';
  } catch (e) {
    state.recStream?.getTracks().forEach((track) => track.stop());
    state.recStream = null;
    logAndShowUiError('This browser could not initialize audio recording.', { error: e?.message || String(e) });
    return;
  }

  chunks = [];
  recorder.ondataavailable = (e) => { if (e.data?.size) chunks.push(e.data); };
  recorder.onerror = (e) => logAndShowUiError('Recording failed unexpectedly.', { error: e?.error?.message || 'unknown recorder error' });
  recorder.start();
  state.recording = true;
  state.recStart = Date.now();
  state.status = 'Recording';
  render();
  clientLog('recording.start', 'hold-to-talk recording started', { mimeType: state.recMimeType });
  state.recTimer = setInterval(() => render(), 250);
}

async function stopRec() {
  if (!recorder || recorder.state === 'inactive') return;
  try {
    recorder.stop();
    await new Promise((resolve) => {
      recorder.onstop = resolve;
    });
  } catch (e) {
    logAndShowUiError('Failed to stop recording cleanly.', { error: e?.message || String(e) });
  }

  clearInterval(state.recTimer);
  state.recording = false;
  state.status = 'Thinking';
  state.isTranscribing = true;
  render();

  try {
    const blob = new Blob(chunks, { type: state.recMimeType || 'audio/webm' });
    if (!blob.size) throw new Error('No audio was captured.');
    const fd = new FormData();
    fd.append('file', blob, recordingFilenameForMime(state.recMimeType));

    const r = await fetch('/api/stt', { method: 'POST', body: fd });
    let j = {};
    try {
      j = await r.json();
    } catch (_e) {
      throw new Error(`STT request failed (${r.status}) and returned invalid JSON.`);
    }

    if (!r.ok) {
      const detail = j?.error || `STT request failed with status ${r.status}.`;
      throw new Error(detail);
    }

    if (j.text) {
      document.getElementById('chatInput').value = j.text;
    } else {
      clientLog('stt.empty', 'stt request returned no text', { language: j?.language || '' });
    }
    clientLog('stt.success', 'stt request completed', { language: j?.language || '', chars: (j.text || '').length });
  } catch (e) {
    logAndShowUiError(e.message || 'Failed to transcribe audio.', { phase: 'stt', error: e?.message || String(e) });
    clientLog('stt.error', 'stt request failed', { error: e?.message || String(e) });
  } finally {
    state.isTranscribing = false;
    state.recStream?.getTracks().forEach((track) => track.stop());
    state.recStream = null;
    recorder = null;
    chunks = [];
    state.status = 'Idle';
    render();
  }
}

function authView() {
  return el('div', { class: 'main-pane glass' }, [
    el('h2', { textContent: 'Nice Assistant Login' }),
    state.authError ? el('div', { class: 'error-banner', textContent: state.authError }) : null,
    state.settingsSavedAt ? el('div', { class: 'success-banner', textContent: 'Account created. Please sign in.' }) : null,
    el('input', { id: 'u', class: 'search-input', placeholder: 'username' }),
    el('input', { id: 'p', class: 'search-input', placeholder: 'password', type: 'password' }),
    el('div', { class: 'chips' }, [
      el('button', {
        class: 'pill-btn', textContent: 'Create account', ariaLabel: 'Create account', onclick: async () => {
          const username = (document.getElementById('u')?.value || '').trim();
          const password = document.getElementById('p')?.value || '';
          state.authError = '';
          render();
          try {
            await api('/api/users', { method: 'POST', body: JSON.stringify({ username, password }) });
            state.settingsSavedAt = Date.now();
            if (state.settingsToastTimer) clearTimeout(state.settingsToastTimer);
            state.settingsToastTimer = setTimeout(() => { state.settingsSavedAt = 0; state.settingsToastTimer = 0; render(); }, 2400);
          } catch (e) {
            state.authError = e.message || 'Unable to create account.';
            render();
          }
        },
      }),
      el('button', {
        class: 'send-btn', textContent: 'Login', ariaLabel: 'Login', onclick: async () => {
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
  await runOnboardingWizard();
}

function statusClass() {
  return state.status === 'Listening' || state.status === 'Recording'
    ? 'status-recording'
    : state.status === 'Speaking'
      ? 'status-speaking'
      : state.status === 'Thinking'
        ? 'status-thinking'
        : 'status-idle';
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
  const ttsModelInput = el('input', {
    class: 'search-input',
    value: persona.preferred_tts_model || '',
    placeholder: 'Persona TTS model (optional)',
  });
  const ttsVoiceInput = el('input', {
    class: 'search-input',
    value: persona.preferred_voice || '',
    placeholder: 'Persona voice (optional, falls back to Default voice)',
  });
  const ttsSpeedInput = el('input', {
    type: 'number',
    min: 0.25,
    max: 4,
    step: 0.05,
    class: 'search-input',
    value: persona.preferred_tts_speed || '1',
    placeholder: '1.0',
  });
  const workspaceOptions = state.workspaces.map((w) => {
    const input = el('input', { type: 'checkbox', checked: (persona.workspace_ids || [persona.workspace_id]).includes(w.id) });
    return { id: w.id, input };
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
    el('label', { textContent: 'Assigned workspaces' }),
    el('div', { class: 'persona-gender-grid' }, workspaceOptions.map((opt) => el('label', { class: 'checkbox-row' }, [opt.input, state.workspaces.find((w) => w.id === opt.id)?.name || opt.id]))),
    el('label', { textContent: 'Preferred TTS model' }),
    ttsModelInput,
    el('label', { textContent: 'Preferred TTS voice' }),
    ttsVoiceInput,
    el('label', { textContent: 'Preferred voice speed' }),
    ttsSpeedInput,
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
          const workspaceIds = workspaceOptions.filter((opt) => opt.input.checked).map((opt) => opt.id);
          if (!workspaceIds.length) { state.settingsError = 'A persona must belong to at least one workspace.'; render(); return; }
          await api(`/api/personas/${persona.id}`, {
            method: 'PUT',
            body: JSON.stringify({
              name: nameInput.value.trim() || persona.name,
              system_prompt: systemPromptInput.value,
              default_model: modelSelect.value,
              avatar_url: avatarInput.value,
              personality_details: personalityInput.value,
              preferred_voice: ttsVoiceInput.value.trim(),
              preferred_tts_model: ttsModelInput.value.trim(),
              preferred_tts_speed: ttsSpeedInput.value || '1',
              workspace_id: workspaceIds[0],
              workspace_ids: workspaceIds,
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
        const ok = await confirmModal({ title: 'Delete persona', message: `Delete persona "${persona.name}"?`, confirmText: 'Delete persona' });
        if (!ok) return;
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
      if (state.settingsToastTimer) clearTimeout(state.settingsToastTimer);
      state.settingsToastTimer = setTimeout(() => { state.settingsSavedAt = 0; state.settingsToastTimer = 0; render(); }, 3200);
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
    if (!(w.id in state.workspaceSectionExpanded)) state.workspaceSectionExpanded[w.id] = false;
    const open = Boolean(state.workspaceSectionExpanded[w.id]);
    const currentPersonas = state.personas.filter((p) => (p.workspace_ids || [p.workspace_id]).includes(w.id));
    return el('div', { class: 'persona-card' }, [
      el('div', { class: 'persona-card-header' }, [
        el('button', { class: 'pill-btn', textContent: `${open ? '▾' : '▸'} ${w.name}`, onclick: () => { state.workspaceSectionExpanded[w.id] = !open; render(); } }),
        el('div', { class: 'chips' }, [
          el('button', { class: 'icon-btn', textContent: 'Rename', onclick: async () => {
            const name = await promptModal({ title: 'Rename workspace', initialValue: w.name, confirmText: 'Rename' });
            if (!name?.trim()) return;
            try { await api(`/api/workspaces/${w.id}`, { method: 'PUT', body: JSON.stringify({ name: name.trim() }) }); await refresh(); }
            catch (e) { state.settingsError = e.message; render(); }
          } }),
          el('button', { class: 'icon-btn', textContent: 'Delete', onclick: async () => {
            const ok = await confirmModal({ title: 'Delete workspace', message: `Delete workspace "${w.name}"?`, confirmText: 'Delete workspace' });
            if (!ok) return;
            try { await api(`/api/workspaces/${w.id}`, { method: 'DELETE' }); await refresh(); }
            catch (e) { state.settingsError = e.message; render(); }
          } }),
        ]),
      ]),
      open ? el('div', { class: 'persona-card-body' }, [
        el('div', { class: 'meta', textContent: 'Personas in this workspace' }),
        ...(currentPersonas.length ? currentPersonas.map((p) => managerRow(p.name, [
          el('button', { class: 'icon-btn', textContent: 'Remove', onclick: async () => {
            const ids = (p.workspace_ids || [p.workspace_id]).filter((wid) => wid !== w.id);
            if (!ids.length) { state.settingsError = 'A persona must belong to at least one workspace.'; render(); return; }
            try { await api(`/api/personas/${p.id}`, { method: 'PUT', body: JSON.stringify({ workspace_ids: ids, workspace_id: ids[0] }) }); await refresh(); }
            catch (e) { state.settingsError = e.message; render(); }
          } }),
        ])) : [el('div', { class: 'meta', textContent: 'No personas assigned yet.' })]),
      ]) : null,
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
        [el('option', { value: '', textContent: 'Auto' }), ...state.models.map((m) => el('option', { value: m, textContent: m, selected: m === state.settings.global_default_model }))]),
      el('label', { class: 'checkbox-row' }, [
        el('input', { type: 'checkbox', checked: Boolean(state.settings.general_show_system_messages), onchange: (e) => {
          setVal('general_show_system_messages', e.target.checked);
          setShowSystemMessages(e.target.checked, false);
        } }),
        'Show system/tool messages by default',
      ]),
      el('label', { class: 'checkbox-row' }, [
        el('input', { type: 'checkbox', checked: Boolean(state.settings.general_show_thinking), onchange: (e) => {
          setVal('general_show_thinking', e.target.checked);
          setShowThinkingByDefault(e.target.checked, false);
        } }),
        'Show model thinking by default in all chats',
      ]),
      el('label', { class: 'checkbox-row' }, [
        el('input', { type: 'checkbox', checked: state.settings.general_auto_logout !== false, onchange: (e) => {
          setVal('general_auto_logout', e.target.checked);
          noteActivity();
          clientLog('settings.change', 'auto logout updated', { value: e.target.checked });
        } }),
        'Auto logout after inactivity',
      ]),
      el('button', { class: 'pill-btn', textContent: 'Download log', onclick: () => {
        clientLog('settings.download_log', 'download log clicked');
        window.open('/api/logs/download', '_blank', 'noopener');
      } }),
    ],
    TTS: [
      el('label', { textContent: 'Provider' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('tts_provider', e.target.value) }, ['disabled', 'openai', 'local'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.tts_provider }))),
      el('label', { textContent: 'Audio format' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('tts_format', e.target.value) }, ['wav', 'mp3', 'opus'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.tts_format }))),
      el('label', { textContent: 'Default voice' }),
      el('input', { class: 'search-input', value: state.settings.tts_voice, oninput: (e) => setVal('tts_voice', e.target.value) }),
      el('label', { textContent: 'Default TTS model' }),
      el('input', { class: 'search-input', value: state.settings.tts_model, oninput: (e) => setVal('tts_model', e.target.value) }),
      el('label', { textContent: 'Default voice speed' }),
      el('input', { type: 'number', min: 0.25, max: 4, step: 0.05, class: 'search-input', value: state.settings.tts_speed, oninput: (e) => setVal('tts_speed', e.target.value || '1') }),
    ],
    STT: [
      el('label', { textContent: 'Provider' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('stt_provider', e.target.value) }, ['disabled', 'openai', 'local'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.stt_provider }))),
      el('label', { textContent: 'Language' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('stt_language', e.target.value) }, STT_LANGUAGES.map((x) => el('option', { value: x.value, textContent: x.label, selected: x.value === state.settings.stt_language }))),
      el('label', { class: 'checkbox-row' }, [
        el('input', { type: 'checkbox', checked: Boolean(state.settings.stt_store_recordings), onchange: (e) => setVal('stt_store_recordings', e.target.checked) }),
        'Store voice recordings for debugging (off by default)',
      ]),
    ],
    'Image Generation': [
      el('label', { textContent: 'Provider' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('image_provider', e.target.value) }, ['disabled', 'openai', 'local'].map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.image_provider }))),
      el('label', { textContent: 'Size' }),
      el('select', { class: 'chip-select', onchange: (e) => setVal('image_size', e.target.value) }, SUPPORTED_IMAGE_SIZES.map((x) => el('option', { value: x, textContent: x, selected: x === state.settings.image_size }))),
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
          const contentText = await promptModal({ title: 'Add global memory', initialValue: '', placeholder: 'Memory text', confirmText: 'Add memory' });
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
        const name = await promptModal({ title: 'Persona name', initialValue: 'Assistant', confirmText: 'Add persona' });
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
        const name = await promptModal({ title: 'Workspace name', initialValue: 'New workspace', confirmText: 'Add workspace' });
        if (!name?.trim()) return;
        try { await api('/api/workspaces', { method: 'POST', body: JSON.stringify({ name: name.trim() }) }); await refresh(); }
        catch (e) { state.settingsError = e.message; render(); }
      } }),
    ],
    Models: [
      el('label', { textContent: 'Default model' }),
      el('select', { class: 'chip-select', onchange: (e) => { setVal('global_default_model', e.target.value); if (!state.activeModelSettingsId) state.activeModelSettingsId = e.target.value; } },
        [el('option', { value: '', textContent: 'Auto' }), ...state.models.map((m) => el('option', { value: m, textContent: m, selected: m === state.settings.global_default_model }))]),
      el('label', { textContent: 'Model-specific tuning' }),
      el('select', { class: 'chip-select', value: state.activeModelSettingsId, onchange: (e) => { state.activeModelSettingsId = e.target.value; render(); } },
        [el('option', { value: '', textContent: state.models.length ? 'Select model…' : 'No models found' }), ...state.models.map((m) => el('option', { value: m, textContent: m, selected: m === state.activeModelSettingsId }))]),
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
        el('button', { class: 'icon-btn', textContent: '✕ Close', ariaLabel: 'Close settings', onclick: () => { state.showSettings = false; render(); } }),
        el('button', { class: 'send-btn', textContent: state.settingsSaving ? 'Saving…' : 'Save all', disabled: state.settingsSaving, onclick: persistSettings }),
      ]),
    ]),
    state.settingsError ? el('div', { class: 'error-banner', textContent: state.settingsError }) : null,
    state.settingsSavedAt ? el('div', { class: 'success-banner toast', textContent: 'Settings saved' }) : null,
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
  const messageBody = el('div', { html: md(visibleText || m.text || '') });
  bindMessageImagePreview(messageBody);
  const personaAvatar = state.personas.find((p) => p.id === (personaId || state.selectedPersonaId))?.avatar_url || DEFAULT_PERSONA_AVATAR;
  return el('div', { class: `msg-wrap ${isUser ? 'user' : ''}` }, [
    !isUser && m.role === 'assistant' ? el('img', { class: 'msg-avatar', src: personaAvatar, alt: `${personaName} avatar` }) : null,
    el('article', { class: `msg ${isUser ? 'user' : 'assistant'}` }, [
      el('small', { textContent: roleLabel }),
      hasThinking && showThinking ? el('details', { class: 'think-block', open: true }, [
        el('summary', { textContent: 'Model thinking' }),
        el('div', { class: 'think-content', html: md(thinking) }),
      ]) : null,
      messageBody,
      el('div', { class: 'msg-actions' }, [
        m.role === 'assistant' ? el('button', {
          class: 'icon-btn',
          textContent: '🖼',
          title: 'Generate an image from this reply',
          onclick: async () => { await generateImageFromAssistantMessage(m); },
        }) : null,
        hasThinking ? el('button', {
          class: 'icon-btn',
          textContent: showThinking ? '🙈' : '💭',
          title: showThinking ? 'Hide thinking' : 'Show thinking',
          onclick: () => {
            if (state.showThinkingByDefault) {
              setShowThinkingByDefault(false, false);
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
  const selectedPersona = state.personas.find((p) => p.id === selectedPersonaId);
  const personaName = selectedPersona?.name || 'Persona';
  const personaAvatar = selectedPersona?.avatar_url || DEFAULT_PERSONA_AVATAR;

  const chatList = state.chats
    .filter((c) => (c.title || '').toLowerCase().includes(state.chatSearch.toLowerCase()))
    .map((c) => el('div', { class: `chat-row ${c.id === state.currentChat?.id ? 'active' : ''}`, onclick: () => openChat(c) }, [
      el('div', { class: 'title', textContent: c.title || 'Untitled chat' }),
      el('div', { class: 'meta', textContent: fmtDate(c.updated_at || c.created_at) }),
      el('div', { class: 'chat-actions' }, [
        el('button', { class: 'icon-btn', textContent: '✎', title: 'Rename chat', ariaLabel: 'Rename chat', onclick: async (e) => { e.stopPropagation(); await renameChat(c); } }),
        el('button', { class: 'icon-btn', textContent: '🗑', title: 'Hide chat', ariaLabel: 'Hide chat', onclick: async (e) => { e.stopPropagation(); await hideChat(c.id); } }),
      ]),
    ]));

  const drawer = el('aside', { class: `drawer glass ${state.drawerOpen ? 'open' : ''}`, ariaLabel: 'Chat list' }, [
    el('div', { class: 'drawer-head' }, [
      el('strong', { textContent: 'Chats' }),
      el('button', { class: 'icon-btn', textContent: '✕', title: 'Hide panel', ariaLabel: 'Hide chat list', onclick: () => { state.drawerOpen = false; render(); } }),
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

  const composerBusy = state.isSending || state.isTranscribing || state.isSynthesizing;
  const composer = el('div', { class: 'composer' }, [
    el('textarea', {
      id: 'chatInput',
      rows: 1,
      class: 'composer-input',
      value: state.draftMessage,
      placeholder: 'Ask anything… (Shift+Enter for new line)',
      disabled: composerBusy,
      oninput: (e) => { state.draftMessage = e.target.value; autoResizeComposer(e.target); },
      onkeydown: (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          sendChat(e.currentTarget.value);
        }
      },
      onfocus: (e) => autoResizeComposer(e.target),
    }),
    el('button', { class: 'send-btn', textContent: state.isSending ? 'Sending…' : 'Send', disabled: composerBusy, onclick: () => sendChat(state.draftMessage) }),
    el('button', {
      class: `talk-btn ${state.recording ? 'active' : ''}`,
      textContent: state.recording ? `Recording ${Math.floor((Date.now() - state.recStart) / 1000)}s` : (state.isTranscribing ? 'Transcribing…' : 'Hold to Talk'),
      disabled: composerBusy && !state.recording,
      onpointerdown: startRec,
      onpointerup: stopRec,
      onpointercancel: stopRec,
      onpointerleave: (e) => { if (e.buttons === 1) stopRec(); },
      onlostpointercapture: stopRec,
    }),
  ]);

  const topbar = el('div', { class: 'topbar' }, [
    el('button', { class: 'icon-btn', textContent: '☰', ariaLabel: 'Toggle chat list', onclick: () => { state.drawerOpen = !state.drawerOpen; render(); } }),
    el('div', { class: 'header-meta' }, [
      el('img', { class: 'topbar-avatar', src: personaAvatar, alt: `${personaName} avatar`, onclick: () => { state.personaAvatarPreview = personaAvatar; render(); } }),
      el('div', { class: 'header-title', textContent: currentChatTitle }),
      el('div', { class: 'chips' }, [el('button', { class: 'chip', textContent: personaName }), el('button', { class: 'chip', textContent: activeWorkspace }), el('button', { class: 'chip', textContent: modelNickname(state.currentChat?.model_override || state.settings?.global_default_model || 'model') })]),
    ]),
    el('div', { class: `status-pill ${statusClass()}`, textContent: state.status }),
    el('button', { class: 'icon-btn', title: 'Logout', ariaLabel: 'Logout', textContent: '⇥', onclick: async () => { await api('/api/logout', { method: 'POST' }); await refresh(); } }),
    el('button', { class: 'icon-btn', textContent: state.showViz ? '◎' : '◉', title: 'Visualizer', ariaLabel: 'Toggle visualizer', onclick: () => setVizVisible(!state.showViz) }),
    el('button', { class: 'icon-btn', textContent: '⚙', title: 'Settings', ariaLabel: 'Open settings', onclick: () => { state.showSettings = true; render(); } }),
  ]);

  const main = el('main', { class: 'main-pane glass' }, [
    topbar,
    el('div', { class: 'chips selector-row' }, [
      el('select', {
        id: 'modelSel', class: 'chip-select compact-select', value: selectedModel, disabled: state.isSending, onchange: (e) => { state.selectedModel = e.target.value; },
      }, state.models.map((m) => el('option', { value: m, textContent: modelNickname(m), selected: m === selectedModel }))),
      el('select', {
        id: 'memSel', class: 'chip-select compact-select', value: selectedMemoryMode, disabled: state.isSending, onchange: (e) => { state.selectedMemoryMode = e.target.value; },
      }, ['off', 'manual', 'auto'].map((m) => el('option', { value: m, textContent: `Memory: ${m}`, selected: m === selectedMemoryMode }))),
      el('button', { class: 'pill-btn', textContent: state.showSystemMessages ? 'Hide system/tool' : 'Show system/tool', onclick: () => setShowSystemMessages(!state.showSystemMessages) }),
      el('button', { class: 'pill-btn', textContent: state.showThinkingByDefault ? 'Hide thinking' : 'Show thinking', onclick: () => setShowThinkingByDefault(!state.showThinkingByDefault) }),
      el('button', { class: 'pill-btn', textContent: state.voiceResponsesEnabled ? 'Voice replies: On' : 'Voice replies: Off', onclick: () => setVoiceResponsesEnabled(!state.voiceResponsesEnabled) }),
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
  const chatImagePreviewModal = state.chatImagePreview ? el('div', {
    class: 'modal-backdrop avatar-preview-backdrop',
    onclick: (e) => { if (e.target === e.currentTarget) { state.chatImagePreview = ''; render(); } },
  }, [
    el('div', { class: 'avatar-preview-frame' }, [
      el('button', { class: 'icon-btn avatar-preview-close', textContent: '✕', ariaLabel: 'Close image preview', onclick: () => { state.chatImagePreview = ''; render(); } }),
      el('img', { class: 'avatar-preview-full', src: state.chatImagePreview, alt: 'Generated image preview' }),
    ]),
  ]) : null;
  const avatarPreviewModal = state.personaAvatarPreview ? el('div', {
    class: 'modal-backdrop avatar-preview-backdrop',
    onclick: (e) => { if (e.target === e.currentTarget) { state.personaAvatarPreview = ''; render(); } },
  }, [
    el('div', { class: 'avatar-preview-frame' }, [
      el('button', { class: 'icon-btn avatar-preview-close', textContent: '✕', ariaLabel: 'Close avatar preview', onclick: () => { state.personaAvatarPreview = ''; render(); } }),
      el('img', { class: 'avatar-preview-full', src: state.personaAvatarPreview, alt: 'Persona avatar preview' }),
    ]),
  ]) : null;
  const genericModal = state.modal ? el('div', {
    class: 'modal-backdrop',
    onclick: (e) => { if (e.target === e.currentTarget) closeModal(); },
  }, [
    el('div', { class: 'modal-card glass', role: 'dialog', ariaModal: true }, [
      el('h3', { textContent: state.modal.title || 'Confirm action' }),
      state.modal.message ? el('p', { class: 'meta', textContent: state.modal.message }) : null,
      state.modal.kind === 'prompt' ? el('textarea', {
        id: 'modalPromptInput',
        class: 'search-input',
        rows: 3,
        value: state.modal.inputValue || '',
        placeholder: state.modal.inputPlaceholder || '',
        oninput: (e) => { state.modal.inputValue = e.target.value; },
      }) : null,
      el('div', { class: 'modal-actions' }, (state.modal.actions || []).map((action) => el('button', {
        class: action.className || 'pill-btn',
        textContent: action.label,
        disabled: action.disabled,
        onclick: () => runModalAction(action),
      }))),
    ]),
  ]) : null;
  const shellChildren = state.showSettings ? [settingsPanel(), avatarPreviewModal, chatImagePreviewModal, genericModal] : [scrim, drawer, main, jumpBtn, viz, newChatPersonaModal, avatarPreviewModal, chatImagePreviewModal, genericModal];
  app.append(el('div', { class: 'app-shell' }, shellChildren));
  requestAnimationFrame(() => {
    restoreMessagePaneScroll();
    const pane = document.getElementById('messagesPane');
    if (pane) onMessageScroll({ currentTarget: pane });
    const composerInput = document.getElementById('chatInput');
    if (composerInput) autoResizeComposer(composerInput);
    if (state.modal) {
      const modalInput = document.getElementById('modalPromptInput');
      const modalRoot = app.querySelector('.modal-card');
      if (modalInput) modalInput.focus();
      else if (modalRoot) modalRoot.querySelector('button, textarea, input')?.focus();
    }
  });
}

window.addEventListener('error', (e) => {
  clientLog('browser.error', 'window error', { message: e.message, source: e.filename, line: e.lineno, column: e.colno });
});
window.addEventListener('unhandledrejection', (e) => {
  clientLog('browser.unhandledrejection', 'promise rejection', { reason: String(e.reason || '') });
});
window.addEventListener('online', () => clientLog('browser.network', 'network online'));
window.addEventListener('offline', () => clientLog('browser.network', 'network offline'));
document.addEventListener('visibilitychange', () => {
  clientLog('browser.visibility', 'visibility changed', { state: document.visibilityState });
});
document.addEventListener('click', (e) => {
  const target = e.target?.closest('button, a, .chip, .icon-btn');
  if (!target) return;
  const label = (target.textContent || target.getAttribute('title') || target.id || target.className || '').trim().slice(0, 80);
  clientLog('ui.click', 'click interaction', { label, tag: target.tagName });
});
document.addEventListener('change', (e) => {
  const target = e.target;
  if (!target) return;
  const tag = target.tagName;
  if (!['SELECT', 'INPUT', 'TEXTAREA'].includes(tag)) return;
  const field = target.id || target.name || target.className || tag;
  clientLog('ui.change', 'input changed', { field: String(field).slice(0, 80), tag });
});

refresh().then(async () => {
  await clientLog('app.ready', 'application ready', { userId: state.user?.id || '' });
  ensureWizard();
});
