import { randomBytes, timingSafeEqual } from 'node:crypto';

export function createCapability() {
  return randomBytes(32).toString('base64url');
}

export function isLoopback(address) {
  const value = String(address ?? '').toLowerCase().replace(/^\[|\]$/g, '');
  return value === '127.0.0.1' || value === '::1' || value === '0:0:0:0:0:0:0:1' || value === '::ffff:127.0.0.1';
}

export function verifyCapability(actual, expected) {
  if (typeof actual !== 'string' || typeof expected !== 'string') return false;
  const left = Buffer.from(actual);
  const right = Buffer.from(expected);
  return left.length === right.length && timingSafeEqual(left, right);
}

export function secureHeaders(contentType = 'application/json; charset=utf-8') {
  return {
    'content-type': contentType,
    'cache-control': 'no-store',
    'content-security-policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'",
    'referrer-policy': 'no-referrer',
    'x-content-type-options': 'nosniff',
    'x-frame-options': 'DENY',
    'cross-origin-opener-policy': 'same-origin',
  };
}

export function authorizeRequest(request, { expectedHost, expectedOrigin, capability, requireOrigin = false }) {
  if (!isLoopback(request.socket.remoteAddress)) throw new Error('NON_LOOPBACK');
  if (request.headers.host !== expectedHost) throw new Error('HOST_MISMATCH');
  const origin = request.headers.origin;
  if ((requireOrigin || origin) && origin !== expectedOrigin) throw new Error('ORIGIN_MISMATCH');
  const authorization = request.headers.authorization ?? '';
  if (!authorization.startsWith('Bearer ') || !verifyCapability(authorization.slice(7), capability)) throw new Error('CAPABILITY_MISMATCH');
}
