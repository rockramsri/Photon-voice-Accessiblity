/**
 * Inbound agent — authenticate a caller, then schedule an outbound callback.
 * No order details are ever discussed inbound. The agent collects the account
 * phone + date of birth, finds the caller's latest order (Clinical API, M2M
 * token), verifies the DOB, and POSTs the order to the server, which dials back
 * after CALLBACK_DELAY_S. The callback runs the normal outbound order flow.
 */
import { llm, voice, type JobContext, type RunContext } from '@livekit/agents';
import { z } from 'zod';
import { getM2mToken } from '../auth/photonAuth.js';
import { config } from '../config.js';
import { getLogger } from '../log.js';
import * as client from '../photon/client.js';
import * as queries from '../photon/queries.js';
import { buildSession } from './sessionFactory.js';

const log = getLogger('photon.inbound');

interface InboundData {
  attempts: number;
  endCall: () => void;
}
type Ctx = RunContext<InboundData>;

const INSTRUCTIONS = `You are ${config.AGENT_DISPLAY_NAME}, a phone coordinator for ${config.ORG_NAME}. You're on a PHONE CALL.

HOW TO SPEAK
- Plain spoken words only. No markdown, lists, symbols, or emojis.
- One or two short sentences per turn. One question per turn, never two.
- Numbers in words. Read nothing back that you weren't given.
- Lead each turn with one warm expression tag and vary it, e.g. <expr type="expression" label="happy"/> to greet, <expr type="expression" label="empathetic"/> if they sound worried, <expr type="expression" label="grateful"/> when they thank you. Never put a tag inside a phone number or a date.

WHAT YOU CAN DO
- You CANNOT look up, confirm, or discuss any order, medication, pharmacy, or price on this call. You can ONLY arrange a callback about their order.
- Greet them and ask how you can help. If it's anything about a prescription, order, pharmacy, or delivery, offer to have someone call them back about it.
- To arrange it you need two things, asked ONE at a time: first the phone number on their account, then their date of birth.
- Once you have both, call request_callback with the phone digits and the month, day, and year. Then say exactly what its result tells you.
- Never reveal or guess a date of birth. Never promise anything beyond a callback. If they're upset or ask for a person, give them the support number and end the call.
`;

function normalizePhone(raw: string): string | null {
  const digits = (raw || '').replace(/\D/g, '');
  if (digits.length === 11 && digits.startsWith('1')) return `+${digits}`;
  if (digits.length === 10) return `+1${digits}`;
  return null;
}

async function end(ctx: Ctx, line: string): Promise<string> {
  const handle = ctx.session.say(line);
  handle.addDoneCallback(() => ctx.userData.endCall());
  throw new voice.StopResponse();
}

export const requestCallback = llm.tool({
  name: 'request_callback',
  description: "Find the caller's latest order by phone, verify their date of birth, and schedule a callback.",
  parameters: z.object({
    phone_digits: z.string().describe('The account phone number, digits only (10 digits, or 11 with a leading 1).'),
    month: z.number().int().describe('Date of birth month, 1 to 12.'),
    day: z.number().int().describe('Date of birth day, 1 to 31.'),
    year: z.number().int().describe('Date of birth four-digit year, e.g. 2002.'),
  }),
  execute: async ({ phone_digits, month, day, year }, { ctx }: { ctx: Ctx }) => {
    const state = ctx.userData;
    const phone = normalizePhone(phone_digits);
    if (!phone) return "That phone number wasn't ten digits. Ask them to say it again, digit by digit.";

    let orders: any[];
    try {
      const token = await getM2mToken();
      const data = await client.clinicalApi(queries.ORDERS_BY_PHONE, { phone }, token, 8000);
      orders = (data.data?.orders as any[]) ?? [];
    } catch (exc) {
      log.warn(`inbound lookup failed: ${exc}`);
      return 'Our system is slow right now. Apologize and tell them to try again shortly.';
    }

    const support = config.SUPPORT_SMS_NUMBER;
    if (!orders.length) {
      return end(ctx, `<expr type="expression" label="empathetic"/> I'm sorry, I couldn't find an order under that number. Please call us at ${support} and we'll help. Goodbye.`);
    }

    const order = orders[0];
    const dob = String(order?.patient?.dateOfBirth ?? '').slice(0, 10);
    state.attempts = (state.attempts ?? 0) + 1;
    const parts = dob.split('-').map((x) => parseInt(x, 10));
    if (parts.length !== 3 || parts.some((n) => Number.isNaN(n))) {
      return end(ctx, `<expr type="expression" label="empathetic"/> I'm sorry, I can't verify that right now. Please call us at ${support}. Goodbye.`);
    }
    const [y, m, d] = parts;

    if (month !== m || day !== d || year !== y) {
      if (state.attempts < 3) return "That date of birth doesn't match our records. Ask them to say the month, day, and year again, slowly.";
      return end(ctx, `<expr type="expression" label="empathetic"/> I'm sorry, that still doesn't match, so I can't set up a callback. Please call us at ${support}. Goodbye.`);
    }

    const orderId = order.id;
    const patientId = order?.patient?.id;
    const url = config.DISPATCH_CALLBACK_URL || `http://localhost:${config.PORT}/internal/callback`;
    let ok = false;
    try {
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ orderId, patientId }),
        signal: AbortSignal.timeout(5000),
      });
      ok = resp.status === 200;
    } catch (exc) {
      log.warn(`inbound callback enqueue failed order=${orderId}: ${exc}`);
    }
    if (!ok) {
      return end(ctx, `<expr type="expression" label="empathetic"/> I'm sorry, I couldn't set that up right now. Please call us at ${support}. Goodbye.`);
    }

    log.info(`inbound callback scheduled order=${orderId}`);
    return end(ctx, `<expr type="expression" label="grateful"/> Thank you, you're verified. If there's anything for your order, we'll call you right back about it. Take care, goodbye.`);
  },
});

class InboundAgent extends voice.Agent<InboundData> {
  constructor() {
    super({ instructions: INSTRUCTIONS, tools: [requestCallback] as never });
  }

  async onEnter(): Promise<void> {
    this.session.generateReply({
      instructions: `Greet the caller warmly, say you're ${config.AGENT_DISPLAY_NAME} from ${config.ORG_NAME}, and ask how you can help.`,
    });
  }
}

export async function inboundEntrypoint(ctx: JobContext): Promise<void> {
  const data: InboundData = { attempts: 0, endCall: () => ctx.shutdown('inbound call ended') };
  const session = await buildSession<InboundData>(data);
  await ctx.waitForParticipant();
  log.info(`inbound call connected room=${ctx.room.name}`);
  await session.start({ agent: new InboundAgent(), room: ctx.room, record: false });
}
