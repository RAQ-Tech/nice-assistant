import { api, type ApiClient, type MediaJobInput } from './api';
import { errorMessage } from './dom';
import { speechText } from './speech_text';
import { machine, state, type ClientStateMachine } from './state';
import type { AppState, CapabilityRequest, Job, Message } from './types';

const IMAGE_MARKDOWN = /!\[[^\]]*\]\(([^)]+)\)/i;
const VIDEO_MARKDOWN = /\[[^\]]*(?:video|download)[^\]]*\]\(([^)]+)\)/i;

export class MediaController {
  private onChange: () => void = () => undefined;

  constructor(
    private readonly appState: AppState = state,
    private readonly stateMachine: ClientStateMachine = machine,
    private readonly client: ApiClient = api,
  ) {}

  setChangeHandler(handler: () => void): void {
    this.onChange = handler;
  }

  async generateImage(prompt: string, chatId: string | null): Promise<Message | null> {
    const settings = this.requiredSettings();
    const input: MediaJobInput = {
      prompt: prompt.trim(),
      chat_id: chatId,
      provider: settings.image_provider,
      size: settings.image_size,
      quality: settings.image_quality,
      backend: settings.image_local_backend,
      base_url: settings.image_local_base_url,
    };
    return this.generate('image', input);
  }

  async generateVideo(prompt: string, chatId: string | null): Promise<Message | null> {
    const settings = this.requiredSettings();
    return this.generate('video', {
      prompt: prompt.trim(),
      chat_id: chatId,
      provider: settings.video_provider,
      model: settings.video_model,
      size: settings.video_size,
      seconds: settings.video_duration,
    });
  }

  private async generate(kind: 'image' | 'video', input: MediaJobInput): Promise<Message | null> {
    if (!input.prompt) throw new Error(`${kind} prompt is required`);
    let durableRequest: CapabilityRequest | null = null;
    try {
      const accepted = kind === 'image' ? await this.client.imageJob(input) : await this.client.videoJob(input);
      durableRequest = await this.client.capabilityRequest(accepted.capability_request_id);
      await this.refreshChat(input.chat_id ?? null);
      while (['queued', 'running'].includes(durableRequest.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 350));
        durableRequest = await this.client.capabilityRequest(durableRequest.id);
        this.upsert(durableRequest);
        await this.refreshChat(input.chat_id ?? null, false);
        this.onChange();
      }
      await this.refreshChat(input.chat_id ?? null);
      if (durableRequest.status !== 'completed') return null;
      return this.appState.messages.find((message) =>
        message.attachments?.some((attachment) => attachment.capability_request_id === durableRequest?.id),
      ) ?? null;
    } catch (error) {
      if (!durableRequest) {
        this.appState.uiError = errorMessage(error, `${kind} generation could not start.`);
        throw error;
      }
      await this.refreshChat(input.chat_id ?? null).catch(() => undefined);
      return null;
    } finally {
      this.onChange();
    }
  }

  private upsert(request: CapabilityRequest): void {
    this.appState.capabilityRequests = [
      ...this.appState.capabilityRequests.filter((item) => item.id !== request.id),
      request,
    ].sort((left, right) => left.requested_at - right.requested_at);
  }

  private async refreshChat(chatId: string | null, includeCapabilities = true): Promise<void> {
    if (!chatId || this.appState.currentChat?.id !== chatId) return;
    const [detail, capabilities] = await Promise.all([
      this.client.chat(chatId),
      includeCapabilities ? this.client.capabilityRequests(chatId) : Promise.resolve(null),
    ]);
    if (this.appState.currentChat?.id !== chatId) return;
    this.appState.currentChat = detail.chat;
    this.appState.messages = detail.messages;
    if (capabilities) this.appState.capabilityRequests = capabilities.items;
  }

  private requiredSettings() {
    if (!this.appState.settings) throw new Error('Settings are unavailable.');
    return this.appState.settings;
  }
}

export async function waitForJob(
  client: ApiClient,
  jobId: string,
  update?: (job: Job) => void,
  intervalMs = 350,
): Promise<Job> {
  while (true) {
    const job = await client.job(jobId);
    update?.(job);
    if (['completed', 'failed', 'cancelled'].includes(job.status)) return job;
    await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
  }
}

export function mediaMessage(kind: 'image' | 'video', job: Job): Message {
  const result = job.result ?? {};
  const mediaId = typeof result.mediaId === 'string' ? result.mediaId : '';
  const protectedUrl = mediaId ? `/api/v1/media/${encodeURIComponent(mediaId)}` : '';
  let text = typeof result.text === 'string' ? result.text : '';
  if (protectedUrl) {
    text = kind === 'image'
      ? `Here is your generated image.\n\n![Generated image](${protectedUrl})`
      : `Here is your generated video.\n\n[Download generated video](${protectedUrl})`;
  }
  if (!text) text = `${kind} generation completed.`;
  return {
    id: `media-${job.id}`,
    role: 'assistant',
    text,
    created_at: Math.floor(Date.now() / 1000),
  };
}

export function extractImageUrl(text: string): string {
  return IMAGE_MARKDOWN.exec(text)?.[1] ?? '';
}

export function extractVideoUrl(text: string): string {
  return VIDEO_MARKDOWN.exec(text)?.[1] ?? '';
}

export function stripVideoLinks(text: string): string {
  return text.replace(VIDEO_MARKDOWN, '').trim();
}

export { speechText } from './speech_text';

export function imagePromptFromMessage(message: Message): string {
  const source = speechText(message.text).slice(0, 1200);
  return source
    ? `Create a coherent image inspired by this assistant response. Preserve named people, places, objects, mood, and visual style: ${source}`
    : 'Create a coherent image for this conversation.';
}
