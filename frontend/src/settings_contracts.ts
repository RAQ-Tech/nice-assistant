export interface SettingsDialogs {
  prompt(title: string, message: string, initial?: string): Promise<string | null>;
  confirm(title: string, message: string, confirmText?: string): Promise<boolean>;
  info(title: string, message: string): void;
  /** Resolves the chosen option's index. Escape resolves 0, the safe option. */
  choice(title: string, message: string, options: readonly string[]): Promise<number>;
  /** Cancel / confirm with a caller-owned “remember this” checkbox. */
  consent(
    title: string,
    message: string,
    confirmText: string,
    checkboxLabel: string,
  ): Promise<{ ok: boolean; remember: boolean }>;
}
