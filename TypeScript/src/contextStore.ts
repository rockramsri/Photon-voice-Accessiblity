/** context/<orderId>.json — written by the dispatcher, read by the agent worker. */
import fs from 'node:fs';
import path from 'node:path';
import { config } from './config.js';
import type { Json } from './photon/client.js';

export function saveContext(orderId: string, promptBlock: string, seed: Json): void {
  fs.mkdirSync(config.CONTEXT_DIR, { recursive: true });
  const doc = { orderId, promptBlock, seed, shapedAt: new Date().toISOString() };
  fs.writeFileSync(path.join(config.CONTEXT_DIR, `${orderId}.json`), JSON.stringify(doc, null, 2));
}

export function loadContext(orderId: string): Json | null {
  const p = path.join(config.CONTEXT_DIR, `${orderId}.json`);
  return fs.existsSync(p) ? (JSON.parse(fs.readFileSync(p, 'utf8')) as Json) : null;
}
