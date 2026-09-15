import { canonicalJson, sha256 } from '../domain/canonical.mjs';
import { ProcessError, requireProcess } from './errors.mjs';

export const JSON_LIMITS = Object.freeze({ maxBytes: 1048576, maxDepth: 20 });

// JSON.parse alone loses duplicate keys before a schema or hash can inspect them.
export function parseProcessJson(source, limits = JSON_LIMITS) {
  let text;
  if (Buffer.isBuffer(source)) {
    requireProcess(source.length <= limits.maxBytes, 'JSON_TOO_LARGE');
    try { text = new TextDecoder('utf-8', { fatal: true }).decode(source); }
    catch { throw new ProcessError('JSON_INVALID_UTF8'); }
  } else {
    requireProcess(typeof source === 'string', 'JSON_TEXT_REQUIRED');
    requireProcess(Buffer.byteLength(source, 'utf8') <= limits.maxBytes, 'JSON_TOO_LARGE');
    text = source;
  }
  let cursor = 0;
  const whitespace = () => { while (/[\x20\t\r\n]/.test(text[cursor] ?? '\0')) cursor++; };
  const fail = () => { throw new ProcessError('JSON_SYNTAX', { offset: cursor }); };
  function string() {
    const start = cursor++;
    while (cursor < text.length) {
      const value = text[cursor++];
      if (value === '"') {
        try { return JSON.parse(text.slice(start, cursor)); } catch { fail(); }
      }
      if (value === '\\') cursor++;
    }
    fail();
  }
  function value(depth) {
    requireProcess(depth <= limits.maxDepth, 'JSON_TOO_DEEP');
    whitespace();
    const char = text[cursor];
    if (char === '"') return string();
    if (char === '{' || char === '[') {
      const object = char === '{', end = object ? '}' : ']';
      const result = object ? Object.create(null) : [];
      const seen = new Set();
      cursor++; whitespace();
      if (text[cursor] === end) { cursor++; return result; }
      while (cursor < text.length) {
        let key;
        if (object) {
          if (text[cursor] !== '"') fail();
          key = string();
          requireProcess(!seen.has(key), 'JSON_DUPLICATE_KEY', { offset: cursor });
          seen.add(key); whitespace();
          if (text[cursor++] !== ':') fail();
        }
        const item = value(depth + 1);
        if (object) result[key] = item; else result.push(item);
        whitespace();
        if (text[cursor] === end) { cursor++; return result; }
        if (text[cursor++] !== ',') fail();
        whitespace();
      }
      fail();
    }
    for (const [literal, result] of [['true', true], ['false', false], ['null', null]]) {
      if (text.startsWith(literal, cursor)) { cursor += literal.length; return result; }
    }
    const number = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(cursor));
    if (!number) fail();
    cursor += number[0].length;
    const result = Number(number[0]);
    requireProcess(Number.isFinite(result) && (!Number.isInteger(result) || Number.isSafeInteger(result)), 'JSON_UNSAFE_NUMBER');
    return result;
  }
  const result = value(0); whitespace();
  if (cursor !== text.length) fail();
  return result;
}

export function processValue(input, limits = JSON_LIMITS) {
  if (typeof input === 'string' || Buffer.isBuffer(input)) return parseProcessJson(input, limits);
  const ancestors = new Set();
  let budget = limits.maxBytes;
  function copy(value, depth) {
    requireProcess(depth <= limits.maxDepth, 'JSON_TOO_DEEP');
    requireProcess(--budget >= 0, 'JSON_TOO_LARGE');
    if (value === null || typeof value === 'boolean') return value;
    if (typeof value === 'string') { budget -= Buffer.byteLength(value); return value; }
    if (typeof value === 'number') {
      requireProcess(Number.isFinite(value) && (!Number.isInteger(value) || Number.isSafeInteger(value)), 'JSON_UNSAFE_NUMBER');
      return value;
    }
    requireProcess(value && typeof value === 'object', 'JSON_NON_DATA_VALUE');
    const proto = Object.getPrototypeOf(value);
    requireProcess(Array.isArray(value) ? proto === Array.prototype : proto === Object.prototype || proto === null, 'JSON_NON_DATA_VALUE');
    requireProcess(!ancestors.has(value), 'JSON_CYCLE');
    ancestors.add(value);
    const result = Array.isArray(value) ? [] : Object.create(null);
    const descriptors = Object.getOwnPropertyDescriptors(value);
    requireProcess(Object.getOwnPropertySymbols(value).length === 0, 'JSON_NON_DATA_VALUE');
    for (const [key, descriptor] of Object.entries(descriptors)) {
      if (Array.isArray(value) && key === 'length') continue;
      requireProcess(Object.hasOwn(descriptor, 'value') && descriptor.enumerable, 'JSON_NON_DATA_VALUE');
      if (Array.isArray(value)) requireProcess(/^(?:0|[1-9]\d*)$/.test(key) && Number(key) < value.length, 'JSON_NON_DATA_VALUE');
      budget -= Buffer.byteLength(key);
      result[key] = copy(descriptor.value, depth + 1);
    }
    if (Array.isArray(value)) requireProcess(Object.keys(result).length === value.length, 'JSON_SPARSE_ARRAY');
    ancestors.delete(value);
    return result;
  }
  const result = copy(input, 0);
  requireProcess(Buffer.byteLength(canonicalJson(result)) <= limits.maxBytes, 'JSON_TOO_LARGE');
  return result;
}

export function bodyHash(value, field) {
  const body = { ...value }; delete body[field];
  return sha256(body);
}
export const workDefinition = context => Object.fromEntries(['goal', 'expectedOutcome', 'moduleIds', 'plannedPaths', 'criteria'].map(key => [key, context[key]]));
export const sameValue = (left, right) => canonicalJson(left) === canonicalJson(right);
export { canonicalJson, sha256 };
