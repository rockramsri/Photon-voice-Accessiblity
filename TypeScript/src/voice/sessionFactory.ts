/**
 * Build the AgentSession: STT -> LLM -> TTS cascade.
 * STT/LLM/TTS all route through LiveKit Inference (no vendor keys needed). TTS
 * is Fish by default and converts expressive <expr .../> tags into provider markup.
 */
import { ReadableStream } from 'node:stream/web';
import { inference, voice } from '@livekit/agents';
import * as silero from '@livekit/agents-plugin-silero';
import { config } from '../config.js';
import { sanitizeForVoice } from './shapeContext.js';

type VADInstance = Awaited<ReturnType<typeof silero.VAD.load>>;
let vadPromise: Promise<VADInstance> | null = null;
function loadVad(): Promise<VADInstance> {
  if (!vadPromise) vadPromise = silero.VAD.load();
  return vadPromise;
}

const SENTENCE_END = /[.!?](?=\s)|\n/g;

/** Safety net before TTS: sanitize whole sentences ("$3.76" can span tokens). */
function voiceNormalizer(input: ReadableStream<string>): ReadableStream<string> {
  const reader = input.getReader();
  let buf = '';
  return new ReadableStream<string>({
    async pull(controller) {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          if (buf.trim()) controller.enqueue(sanitizeForVoice(buf));
          controller.close();
          return;
        }
        buf += value;
        SENTENCE_END.lastIndex = 0;
        let lastEnd = -1;
        let m: RegExpExecArray | null;
        while ((m = SENTENCE_END.exec(buf))) lastEnd = m.index + m[0].length;
        if (lastEnd >= 0) {
          controller.enqueue(`${sanitizeForVoice(buf.slice(0, lastEnd))} `);
          buf = buf.slice(lastEnd);
          return;
        }
      }
    },
  });
}

function makeStt() {
  return new inference.STT({ model: 'deepgram/nova-3', language: 'en' });
}
function makeLlm() {
  return new inference.LLM({ model: `openai/${config.LLM_MODEL}` });
}
function makeTts() {
  return new inference.TTS({
    model: config.TTS_MODEL as 'fishaudio/s2.1-pro',
    voice: config.TTS_VOICE,
    modelOptions: { speed: config.TTS_SPEED },
  });
}

export async function buildSession<T = unknown>(userData: T): Promise<voice.AgentSession<T>> {
  const session = new voice.AgentSession<T>({
    stt: makeStt(),
    llm: makeLlm(),
    tts: makeTts(),
    vad: await loadVad(),
    userData,
    ttsTextTransforms: ['filter_emoji', 'filter_markdown', voiceNormalizer],
    expressive: config.EXPRESSIVE,
    turnHandling: {
      turnDetection: new inference.TurnDetector({ unlikelyThreshold: config.EOT_THRESHOLD }),
      // Endpointing delays are in milliseconds here (config holds seconds).
      endpointing: { mode: 'dynamic', minDelay: config.EOT_MIN_DELAY * 1000, maxDelay: config.EOT_MAX_DELAY * 1000 },
    },
    userAwayTimeout: config.USER_AWAY_S,
  });

  session.on(voice.AgentSessionEventTypes.UserStateChanged, (ev) => {
    // Fires once per silence; kept out of chat history so the LLM doesn't build on it.
    if (ev.newState === 'away') {
      session.say('<expr type="prosody" label="soft">Take your time, I\'m here.</expr>', { addToChatCtx: false });
    }
  });

  return session;
}
