/** Persisted set of processed orderIds — webhooks redeliver, we dial once. */
import fs from 'node:fs';
import path from 'node:path';
import { config } from '../config.js';
import { getLogger } from '../log.js';

const log = getLogger('photon.dedupe');

function load(): Set<string> {
  try {
    return new Set(JSON.parse(fs.readFileSync(config.STATE_FILE, 'utf8')));
  } catch (exc: any) {
    if (exc?.code === 'ENOENT') return new Set();
    log.error(`could not read dedupe state (${exc}); starting empty`);
    return new Set();
  }
}

export function seen(orderId: string): boolean {
  return load().has(orderId);
}

export function mark(orderId: string): void {
  const ids = load();
  if (ids.has(orderId)) return;
  ids.add(orderId);
  fs.mkdirSync(path.dirname(config.STATE_FILE), { recursive: true });
  fs.writeFileSync(config.STATE_FILE, JSON.stringify([...ids].sort(), null, 2));
}
