export class ProcessError extends Error {
  constructor(code, details = {}) {
    super(code);
    this.name = 'ProcessError';
    this.code = code;
    this.details = details;
  }
}

export function requireProcess(condition, code, details) {
  if (!condition) throw new ProcessError(code, details);
}
