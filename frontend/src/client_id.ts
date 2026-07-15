let fallbackSequence = 0;

type CryptoSource = Pick<Crypto, 'getRandomValues'> & Partial<Pick<Crypto, 'randomUUID'>>;

function uuidFromRandomBytes(source: Pick<Crypto, 'getRandomValues'>): string {
  const bytes = source.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function clientId(
  prefix: string,
  source: CryptoSource | null | undefined = globalThis.crypto,
): string {
  if (typeof source?.randomUUID === 'function') return `${prefix}-${source.randomUUID()}`;
  if (typeof source?.getRandomValues === 'function') return `${prefix}-${uuidFromRandomBytes(source)}`;

  // These IDs only reconcile optimistic browser messages; they are never used
  // for authentication, authorization, or durable server identity.
  fallbackSequence += 1;
  return `${prefix}-${Date.now().toString(36)}-${fallbackSequence.toString(36)}`;
}
