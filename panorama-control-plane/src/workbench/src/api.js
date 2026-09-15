let capability = null;
const CAPABILITY_KEY = 'panorama.capability.v1';

export function initializeCapability() {
  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  capability = fragment.get('cap') || window.sessionStorage.getItem(CAPABILITY_KEY);
  if (fragment.get('cap')) {
    window.sessionStorage.setItem(CAPABILITY_KEY, capability);
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
  }
  return Boolean(capability);
}

export async function apiRequest(path, options = {}) {
  if (!capability) throw new Error('CAPABILITY_REQUIRED');
  const response = await fetch(path, {
    method: options.method ?? 'GET',
    headers: {
      authorization: `Bearer ${capability}`,
      ...(options.body ? { 'content-type': 'application/json' } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
    cache: 'no-store',
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error ?? `HTTP_${response.status}`);
  return value;
}

export function submitCommand(type, input, expectedRevision) {
  return apiRequest('/api/v1/commands', {
    method: 'POST',
    body: {
      formatVersion: 'panorama.workbench-command.v0.1',
      type,
      input,
      expectedRevision,
      idempotencyKey: crypto.randomUUID(),
    },
  });
}
