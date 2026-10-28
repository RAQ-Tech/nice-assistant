/** What a recording is captured as, in the order a browser is asked for it. */
const MIME_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
  'audio/ogg',
];

export function preferredMimeType(): string {
  if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') return '';
  return MIME_TYPES.find((value) => MediaRecorder.isTypeSupported(value)) ?? '';
}

export function recordingFilename(mimeType: string): string {
  if (mimeType.includes('mp4')) return 'audio.mp4';
  if (mimeType.includes('ogg')) return 'audio.ogg';
  return 'audio.webm';
}
