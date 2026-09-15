import { validateDefinition } from './schema.mjs';
import { processValue, sameValue } from './json.mjs';
import { requireProcess } from './errors.mjs';

// Memoized wildcard matching has a bounded state space; patterns never become JS.
export function matchPath(pattern, path) {
  requireProcess(typeof pattern === 'string' && pattern.length <= 512 && typeof path === 'string' && path.length <= 512, 'PATH_LIMIT');
  const memo = new Map();
  function match(i, j) {
    const key = i * (path.length + 1) + j;
    if (memo.has(key)) return memo.get(key);
    let result;
    if (i === pattern.length) result = j === path.length;
    else if (pattern[i] === '*') {
      const double = pattern[i + 1] === '*';
      const next = i + (double ? 2 : 1);
      result = match(next, j) || (double && pattern[next] === '/' && match(next + 1, j)) ||
        (j < path.length && (double || path[j] !== '/') && match(i, j + 1));
    } else result = j < path.length && (pattern[i] === path[j] || (pattern[i] === '?' && path[j] !== '/')) && match(i + 1, j + 1);
    memo.set(key, Boolean(result)); return Boolean(result);
  }
  return match(0, 0);
}

export function evaluateApplicability(expression, { facts = [], paths = [] } = {}) {
  const expr = validateDefinition(expression, 'expression');
  const context = processValue({ facts, paths });
  requireProcess(context.facts.length <= 1000 && context.paths.length <= 100, 'FACT_LIMIT');
  for (const fact of context.facts) {
    requireProcess(typeof fact.key === 'string' && ['declared', 'observed', 'unknown', 'conflict'].includes(fact.status), 'FACT_INVALID');
    requireProcess(fact.value === null || ['string', 'boolean', 'number'].includes(typeof fact.value), 'FACT_INVALID');
    requireProcess(Array.isArray(fact.basisRefs) && fact.basisRefs.length > 0 && fact.basisRefs.every(value => typeof value === 'string' && value.trim()), 'FACT_BASIS_REQUIRED');
  }
  for (const item of context.paths) {
    requireProcess(typeof item.rootId === 'string' && typeof item.complete === 'boolean' && Array.isArray(item.paths) && item.paths.length <= 50000 && item.paths.every(path => typeof path === 'string' && path.length <= 512), 'PATH_FACT_INVALID');
    requireProcess(Array.isArray(item.basisRefs) && item.basisRefs.length > 0 && item.basisRefs.every(value => typeof value === 'string' && value.trim()), 'FACT_BASIS_REQUIRED');
  }
  const result = (status, basisRefs, reason) => ({ status, basisRefs: [...new Set(basisRefs)].sort(), reason });
  function evaluate(item) {
    if (item.always === true) return result('applicable', ['rule:always'], '基础必备规则始终适用。');
    if (Object.hasOwn(item, 'fact')) {
      const rows = context.facts.filter(fact => fact.key === item.fact);
      const refs = rows.flatMap(row => row.basisRefs);
      const values = rows.filter(row => row.value !== null && !['unknown', 'conflict'].includes(row.status)).map(row => row.value);
      if (rows.some(row => row.status === 'conflict') || values.some(value => !sameValue(value, values[0]))) return result('conflict', refs, '适用性事实的来源冲突。');
      if (!rows.length || rows.some(row => row.status === 'unknown' || row.value === null)) return result('unknown', refs.length ? refs : ['fact:missing:' + item.fact], '适用性事实尚不完整。');
      return result(values[0] === item.equals ? 'applicable' : 'not_applicable', refs, '按带来源的事实进行类型严格比较。');
    }
    if (item.paths) {
      const rows = context.paths.filter(row => row.rootId === item.paths.rootId);
      const refs = rows.flatMap(row => row.basisRefs);
      const matched = rows.some(row => row.paths.some(path => item.paths.include.some(pattern => matchPath(pattern, path))));
      return result(matched ? 'applicable' : rows.length && rows.every(row => row.complete) ? 'not_applicable' : 'unknown', refs.length ? refs : ['paths:missing:' + item.paths.rootId], matched ? '声明范围内有匹配变化。' : '未匹配时按文件清单完整性判断。');
    }
    if (item.not) {
      const inner = evaluate(item.not);
      return { ...inner, status: ({ applicable: 'not_applicable', not_applicable: 'applicable' })[inner.status] ?? inner.status };
    }
    const all = Boolean(item.all), children = (item.all ?? item.any).map(evaluate);
    const states = children.map(child => child.status);
    const decisive = all ? 'not_applicable' : 'applicable';
    const status = states.includes('conflict') ? 'conflict' : states.includes(decisive) ? decisive : states.includes('unknown') ? 'unknown' : all ? 'applicable' : 'not_applicable';
    return result(status, children.flatMap(child => child.basisRefs), '合并条件结果，并保留未知和冲突。');
  }
  return evaluate(expr);
}
