/**
 * Photon webhook server — receive order events, greet the patient by voice.
 *   POST /photon/webhooks   raw-body capture -> 200 immediately -> process async
 *   POST /internal/callback inbound agent enqueues an authenticated callback
 *   GET  /health            liveness probe
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as dispatcher from './dispatcher.js';
import { config } from './src/config.js';
import { getLogger } from './src/log.js';
import * as events from './src/photon/events.js';

const log = getLogger('photon.server');
const LOG_FILE = path.join(config.ROOT, 'webhooks.jsonl');
const CREATED = 'photon:order:created';

async function processEvent(ev: Record<string, any>): Promise<void> {
  const evType = ev.type;
  try {
    if (evType === CREATED) await dispatcher.handleOrderCreated(ev.data ?? {});
    else log.info(`ignoring event type=${evType} (id=${ev.id})`);
  } catch (exc) {
    log.error(`handler failed for ${evType}: ${exc}`);
  }
}

async function delayedCallback(orderId: string, patientId: string): Promise<void> {
  await new Promise((r) => setTimeout(r, config.CALLBACK_DELAY_S * 1000));
  log.info(`callback firing order=${orderId} after ${config.CALLBACK_DELAY_S}s`);
  await dispatcher.handleOrderCreated({ id: orderId, patient: { id: patientId } }, true);
}

function readBody(req: http.IncomingMessage): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

function json(res: http.ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(payload);
}

async function handleWebhook(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  const raw = await readBody(req);
  const receivedAt = new Date().toISOString();

  events.verifySignature(
    raw,
    (req.headers[events.TIMESTAMP_HEADER] as string) || '',
    (req.headers[events.SIGNATURE_HEADER] as string) || '',
    config.SHARED_SECRET,
  );

  const parsed = events.parseEvents(raw);
  log.info(`webhook received: ${parsed.length} event(s) [${parsed.map((e) => e.type ?? '?').join(', ') || 'none'}]`);

  try {
    const line = JSON.stringify({ receivedAt, headers: req.headers, events: parsed }) + '\n';
    fs.appendFileSync(LOG_FILE, line);
  } catch (exc) {
    log.error(`could not append to webhooks.jsonl: ${exc}`);
  }

  // Respond 200 immediately; process out of band.
  for (const ev of parsed) void processEvent(ev);
  json(res, 200, { ok: true });
}

async function handleCallback(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
  let body: any = {};
  try {
    body = JSON.parse((await readBody(req)).toString() || '{}');
  } catch {
    /* fall through to validation */
  }
  const { orderId, patientId } = body;
  if (!orderId || !patientId) {
    json(res, 400, { ok: false, error: 'orderId and patientId required' });
    return;
  }
  log.info(`callback queued order=${orderId} in ${config.CALLBACK_DELAY_S}s`);
  void delayedCallback(orderId, patientId);
  json(res, 200, { ok: true, delaySeconds: config.CALLBACK_DELAY_S });
}

export function buildServer(): http.Server {
  return http.createServer((req, res) => {
    const url = req.url ?? '';
    if (req.method === 'POST' && url === '/photon/webhooks') void handleWebhook(req, res);
    else if (req.method === 'POST' && url === '/internal/callback') void handleCallback(req, res);
    else if (req.method === 'GET' && url === '/health') {
      res.writeHead(200, { 'content-type': 'text/plain' });
      res.end('up');
    } else {
      json(res, 404, { ok: false, error: 'not found' });
    }
  });
}

// Run when invoked directly (node dist/server.js / tsx server.ts). Compare
// resolved filesystem paths so spaces in the path don't break the check.
const isMain = Boolean(process.argv[1]) && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  buildServer().listen(config.PORT, () => {
    log.info(`listening on http://localhost:${config.PORT}  (POST /photon/webhooks, POST /internal/callback, GET /health)`);
  });
}
