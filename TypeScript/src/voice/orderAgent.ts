/**
 * The order agent: fixed persona + this order's prompt block + tools.
 * The order details stay OUT of the prompt until identity is verified, so the
 * model physically can't disclose the medication, pharmacy, or price early.
 */
import fs from 'node:fs';
import { voice } from '@livekit/agents';
import { config } from '../config.js';
import type { SessionState } from './sessionState.js';
import { READ_TOOLS, WRITE_TOOLS } from './tools.js';

export class OrderAgent extends voice.Agent<SessionState> {
  private orderBlock: string;
  private revealed = false;

  constructor(instructions: string, tools: unknown[], orderBlock: string) {
    super({ instructions, tools: tools as never });
    this.orderBlock = orderBlock;
  }

  async onEnter(): Promise<void> {
    this.session.generateReply({
      instructions:
        "Introduce yourself and who you're calling for, then ask if you're speaking with the patient by first name.",
    });
  }

  /** Called by verify_identity on success — add the order block to the prompt. */
  async revealContext(): Promise<void> {
    if (this.revealed || !this.orderBlock) return;
    this.revealed = true;
    await this.updateInstructions((this.instructions as string) + this.orderBlock);
  }
}

export function buildOrderAgent(state: SessionState, promptBlock: string): OrderAgent {
  const persona = fs
    .readFileSync(config.PERSONA_FILE, 'utf8')
    .replaceAll('{agent_name}', config.AGENT_DISPLAY_NAME)
    .replaceAll('{org_name}', state.orgName)
    .replaceAll('{patient_first_name}', state.firstName || 'the patient');
  const tools = [...READ_TOOLS, ...(state.canOfferChange ? WRITE_TOOLS : [])];
  return new OrderAgent(persona, tools, promptBlock);
}
