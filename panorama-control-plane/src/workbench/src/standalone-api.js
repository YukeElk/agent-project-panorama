let capability = null;
const CAPABILITY_KEY = 'panorama.standalone.capability.v1';

export function initializeStandaloneCapability() {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  capability = fragment.get('cap') || window.sessionStorage.getItem(CAPABILITY_KEY);
  if (fragment.get('cap')) {
    window.sessionStorage.setItem(CAPABILITY_KEY, capability);
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
  }
  return Boolean(capability);
}

export async function standaloneRequest(path, { body, signal } = {}) {
  if (!capability) throw new Error('请用启动命令提供的链接打开全景。');
  const response = await fetch(`/api/standalone/${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { authorization: `Bearer ${capability}`, ...(body === undefined ? {} : { 'content-type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body), signal, cache: 'no-store',
  });
  const value = await response.json();
  if (!response.ok) {
    const error = new Error(value.error || `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return value;
}
