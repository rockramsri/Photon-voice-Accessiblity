/**
 * Outbound calls — dispatch the agent into a room, then dial out via SIP.
 * Agent-first ordering so the callee never answers into an empty room.
 * Never throws — any config/SIP/dispatch error comes back as { ok: false, error }.
 */
import { AgentDispatchClient, SipClient } from 'livekit-server-sdk';
import { config } from '../config.js';
import { getLogger } from '../log.js';

const log = getLogger('channels.outbound');

function httpHost(): string {
  return config.LIVEKIT_URL.replace(/^wss:\/\//, 'https://').replace(/^ws:\/\//, 'http://');
}

export interface CallResult {
  ok: boolean;
  to: string;
  room?: string;
  participant?: string;
  error?: string;
}

export async function placeCall(to: string, roomName: string, metadata = ''): Promise<CallResult> {
  if (!config.SIP_OUTBOUND_TRUNK_ID) {
    log.error('SIP_OUTBOUND_TRUNK_ID is not set — cannot place outbound call');
    return { ok: false, to, error: 'outbound SIP trunk not configured' };
  }

  const host = httpHost();
  const dispatchClient = new AgentDispatchClient(host, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET);
  const sipClient = new SipClient(host, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET);

  try {
    // Named worker needs explicit dispatch; the agent reads its context from metadata.
    await dispatchClient.createDispatch(roomName, config.AGENT_NAME, { metadata });
    const participant = await sipClient.createSipParticipant(
      config.SIP_OUTBOUND_TRUNK_ID,
      to,
      roomName,
      {
        participantIdentity: `phone-${to}`,
        fromNumber: config.TWILIO_CALLER_NUMBER,
        waitUntilAnswered: false,
      },
    );
    log.info(`outbound call -> ${to} room=${roomName}`);
    return { ok: true, to, room: roomName, participant: participant.participantIdentity };
  } catch (exc) {
    log.error(`outbound call -> ${to} FAILED: ${exc}`);
    return { ok: false, to, room: roomName, error: String(exc) };
  }
}
