export interface SettingsDialogs {
  prompt(title: string, message: string, initial?: string): Promise<string | null>;
  confirm(title: string, message: string, confirmText?: string): Promise<boolean>;
  info(title: string, message: string): void;
}
