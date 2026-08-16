import type { ApiClient } from './api';
import { errorMessage } from './dom';
import { settingsWire } from './settings';
import type { ClientStateMachine } from './state';
import type { Dialogs } from './settings_view';
import type { AppState } from './types';

/**
 * First-run setup.
 *
 * A flow that happens once, in its own dialogs, and then never again. It lived
 * in `app.ts` beside the routing and the chat shell, which is where anything
 * ends up when nobody decides otherwise; it is here now because it is not part
 * of either.
 */

export async function runFirstRunSetup(
  state: AppState,
  api: ApiClient,
  dialogs: Dialogs,
  machine: ClientStateMachine,
  render: () => void,
): Promise<void> {
  state.onboardingRunning = true;
  try {
    let workspace = state.workspaces[0];
    if (!workspace) {
      const name = await dialogs.prompt('Welcome to Nice Assistant', 'Name your first workspace.', 'Main Workspace');
      if (!name?.trim()) throw new Error('First-run setup needs a workspace.');
      workspace = await api.createWorkspace(name.trim());
      state.workspaces.push(workspace);
    }
    let persona = state.personas[0];
    if (!persona) {
      const name = await dialogs.prompt('Create first persona', 'Give your assistant a persona name.', 'Assistant');
      if (!name?.trim()) throw new Error('First-run setup needs a persona.');
      const prompt = await dialogs.prompt('Default personality', 'Set the initial persona instruction.', 'Be helpful and concise.');
      persona = await api.createPersona({
        workspace_id: workspace.id,
        workspace_ids: [workspace.id],
        name: name.trim(),
        system_prompt: prompt?.trim() || 'Be helpful and concise.',
        default_model: state.models[0] ?? null,
      });
      state.personas.push(persona);
    }
    state.selectedPersonaId = persona.id;
    state.newChatPersonaId = persona.id;
    if (state.settings) {
      state.settings.onboarding_done = true;
      await api.updateSettings(settingsWire(state.settings));
    }
    machine.transition('idle');
  } catch (error) {
    state.uiError = errorMessage(error, 'Unable to complete first-run setup.');
    machine.transition('error');
  } finally {
    state.onboardingRunning = false;
    render();
  }
}
