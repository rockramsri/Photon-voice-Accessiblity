/** Tiny namespaced logger — no PHI, no tokens. Mirrors Python's logging.getLogger. */
export type Logger = {
  info: (msg: string, ...a: unknown[]) => void;
  warn: (msg: string, ...a: unknown[]) => void;
  error: (msg: string, ...a: unknown[]) => void;
};

export function getLogger(name: string): Logger {
  const stamp = () => new Date().toISOString();
  return {
    info: (msg, ...a) => console.log(`${stamp()} INFO  ${name} ${msg}`, ...a),
    warn: (msg, ...a) => console.warn(`${stamp()} WARN  ${name} ${msg}`, ...a),
    error: (msg, ...a) => console.error(`${stamp()} ERROR ${name} ${msg}`, ...a),
  };
}
