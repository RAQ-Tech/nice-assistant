import './styles.css';
import './app';
import { applyRememberedTheme } from './theme';

// Still within the first script task, so before anything is painted.
applyRememberedTheme();

// Installed from the home screen, it needs one; in development it would only get in the way.
if (import.meta.env.PROD && 'serviceWorker' in navigator && window.isSecureContext) {
  void navigator.serviceWorker.register('/sw.js').catch(() => undefined);
}
