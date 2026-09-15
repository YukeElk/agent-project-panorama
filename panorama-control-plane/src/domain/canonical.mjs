import { createHash } from 'node:crypto';

function normalize(value) {
  if (Array.isArray(value)) return value.map(normalize);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, normalize(value[key])]));
  }
  if (value === undefined || typeof value === 'function' || typeof value === 'symbol') {
    throw new TypeError('Domain values must be JSON serializable');
  }
  return value;
}

export function canonicalJson(value) {
  return JSON.stringify(normalize(value));
}

export function sha256(value) {
  const bytes = Buffer.isBuffer(value) ? value : Buffer.from(typeof value === 'string' ? value : canonicalJson(value));
  return createHash('sha256').update(bytes).digest('hex');
}

export function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}
