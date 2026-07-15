import { describe, expect, it, vi } from 'vitest';

import { clientId } from '../src/client_id';

describe('clientId', () => {
  it('uses randomUUID when the browser exposes it', () => {
    const source = {
      randomUUID: vi.fn(() => '00000000-0000-4000-8000-000000000001'),
      getRandomValues: vi.fn(),
    } as unknown as Crypto;
    expect(clientId('message', source)).toBe('message-00000000-0000-4000-8000-000000000001');
    expect(source.randomUUID).toHaveBeenCalledOnce();
  });

  it('creates an RFC 4122 identifier when randomUUID is unavailable on LAN HTTP', () => {
    const source = {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.fill(0xab);
        return bytes;
      },
    } as Crypto;
    expect(clientId('typing', source)).toBe('typing-abababab-abab-4bab-abab-abababababab');
  });

  it('still creates distinct transient identifiers without Web Crypto', () => {
    const first = clientId('local-user', null);
    const second = clientId('local-user', null);
    expect(first).not.toBe(second);
    expect(first).toMatch(/^local-user-[a-z0-9]+-[a-z0-9]+$/);
  });
});
