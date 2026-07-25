import type { ApiClient } from './api';
import { errorMessage } from './dom';
import type { AppState } from './types';

export async function refreshChatBindingsAfterSettingsMutation(
  appState: AppState,
  client: ApiClient,
): Promise<void> {
  if (!appState.currentChat && appState.chats.length === 0) return;
  try {
    const refreshed = (await client.chats()).items;
    const currentId = appState.currentChat?.id;
    appState.chats = refreshed;
    if (currentId) {
      const current = refreshed.find((chat) => chat.id === currentId);
      if (current) appState.currentChat = current;
    }
  } catch (error) {
    appState.settingsError = errorMessage(
      error,
      'The change was saved, but conversation access could not be refreshed.',
    );
  }
}
