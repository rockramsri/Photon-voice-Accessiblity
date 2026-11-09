/**
 * LiveKit worker entrypoint — hold the pharmacy conversation for one order.
 *   tsx src/voice/entry.ts dev        real calls (dispatcher dials, this joins)
 *   tsx src/voice/entry.ts console     local test (set CONSOLE_ORDER_ID, DRY_RUN=1)
 *
 * One agent handles BOTH inbound and outbound (the worker registers a single
 * rtc session). We branch on whether the job carries an order in its metadata.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { cli, defineAgent, ServerOptions, voice, type JobContext } from '@livekit/agents';
import { mintPatientJwt } from '../auth/patientToken.js';
import { config } from '../config.js';
import { loadContext } from '../contextStore.js';
import { getLogger } from '../log.js';
import { buildOrderAgent } from './orderAgent.js';
import { inboundEntrypoint } from './inbound.js';
import { buildSession } from './sessionFactory.js';
import { SessionState } from './sessionState.js';

const log = getLogger('photon.entry');

async function entrypoint(ctx: JobContext): Promise<void> {
  const meta = ctx.job.metadata ? JSON.parse(ctx.job.metadata) : {};
  const orderId: string = meta.orderId || config.CONSOLE_ORDER_ID;

  // Inbound SIP call: no order yet. Authenticate the caller and schedule a callback.
  if (!orderId) {
    await inboundEntrypoint(ctx);
    return;
  }

  // Prefer context from the job metadata (set by the dispatcher). On Railway the
  // worker and web run on different machines, so the local file won't exist here;
  // the file is only a same-machine/console fallback.
  const doc = meta.seed && meta.promptBlock
    ? { seed: meta.seed, promptBlock: meta.promptBlock }
    : loadContext(orderId);
  if (!doc) {
    log.error(`no context for order=${orderId} (metadata + file both empty) — ending`);
    await ctx.deleteRoom().catch(() => {});
    return;
  }

  const seed = doc.seed;
  const jwt = meta.patientJwt || (await mintPatientJwt(orderId, seed.patientId));
  if (!jwt) log.error(`no patient JWT for order=${orderId} — tools won't work`);

  const state = SessionState.fromSeed(seed, jwt || '', config.ORG_NAME);
  state.endCall = () => ctx.shutdown('agent ended call');
  const agent = buildOrderAgent(state, doc.promptBlock);
  const session = await buildSession(state);
  log.info(`order=${orderId} can_change=${state.canOfferChange} console=false`);

  ctx.addShutdownCallback(async () => {
    // Stateless: keep only a one-line outcome, then drop the PHI context file.
    const line = JSON.stringify({
      orderId,
      endedAt: new Date().toISOString(),
      endReason: state.endReason,
      identityVerified: state.identityVerified,
      finalPharmacyId: state.currentPharmacyId,
    }) + '\n';
    fs.appendFileSync(config.CALLS_LOG, line);
    fs.rmSync(path.join(config.CONTEXT_DIR, `${orderId}.json`), { force: true });
    log.info(`call finished order=${orderId} reason=${state.endReason}`);
  });
  ctx.addShutdownCallback(async () => {
    await ctx.deleteRoom().catch(() => {}); // disconnects the SIP leg
  });
  session.on(voice.AgentSessionEventTypes.Close, () => ctx.shutdown('session closed'));

  const participant = await ctx.waitForParticipant();
  log.info(`participant joined: ${participant.identity}`);
  // record=false keeps call audio + transcripts (PHI) out of LiveKit Cloud.
  await session.start({ agent, room: ctx.room, record: false });
}

export default defineAgent({ entry: entrypoint });

cli.runApp(new ServerOptions({ agent: fileURLToPath(import.meta.url), agentName: config.AGENT_NAME }));
