"""Build the AgentSession: STT -> LLM -> TTS cascade.

STT/LLM use the vendor plugin when its API key is set, otherwise LiveKit
Inference. TTS is always Inference (Fish by default), which converts expressive
<expr .../> tags, from the LLM and from static lines, into the provider's markup.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterable

from livekit.agents import AgentSession, inference, llm, stt, tts
from livekit.plugins import deepgram, openai, silero

from shared import config
from voice.shape_context import sanitize_for_voice

_SENTENCE_END = re.compile(r"[.!?](?=\s)|\n")


def _stt() -> stt.STT:
    if config.DEEPGRAM_API_KEY:
        return deepgram.STT(model="nova-3", language="en")
    # Fixed English: "multi" heard "pickup pharmacy" as Hindi, which also swapped the EOT threshold.
    return inference.STT(model="deepgram/nova-3", language="en")


def _llm() -> llm.LLM:
    if config.OPENAI_API_KEY:
        return openai.LLM(model=config.LLM_MODEL)
    return inference.LLM(model=f"openai/{config.LLM_MODEL}")


def _tts() -> tts.TTS:
    return inference.TTS(model=config.TTS_MODEL, voice=config.TTS_VOICE,
                         extra_kwargs={"speed": config.TTS_SPEED})


async def voice_normalizer(text: AsyncIterable[str]) -> AsyncIterable[str]:
    """Safety net before TTS: sanitize whole sentences ("$3.76" can span tokens)."""
    buf = ""
    async for chunk in text:
        buf += chunk
        last = None
        for last in _SENTENCE_END.finditer(buf):
            pass
        if last:
            yield sanitize_for_voice(buf[:last.end()]) + " "
            buf = buf[last.end():]
    if buf.strip():
        yield sanitize_for_voice(buf)


def build_session(userdata=None) -> AgentSession:
    # Light laugh and sighs only; no gasps, yawns, groans or sobbing on a pharmacy call.
    gentle = {"breathing": False, "crying": False, "vocalizing": False, "reflex_sounds": False}
    expressive = (
        {"speech_steering": {"nonverbal_sounds": gentle, "disfluencies": True, "pace": "normal"}}
        if config.EXPRESSIVE else False
    )
    session = AgentSession(
        stt=_stt(),
        llm=_llm(),
        tts=_tts(),
        vad=silero.VAD.load(),
        userdata=userdata,
        tts_text_transforms=["filter_emoji", "filter_markdown", voice_normalizer],
        expressive=expressive,
        turn_handling={
            "turn_detection": inference.TurnDetector(unlikely_threshold=config.EOT_THRESHOLD),
            "endpointing": {"mode": "dynamic", "min_delay": config.EOT_MIN_DELAY,
                            "max_delay": config.EOT_MAX_DELAY},
        },
        user_away_timeout=config.USER_AWAY_S,
    )

    @session.on("user_state_changed")
    def _nudge(ev) -> None:
        # Fires once per silence; kept out of chat history so the LLM doesn't build on it.
        if ev.new_state == "away":
            session.say('<expr type="prosody" label="soft">Take your time, I\'m here.</expr>',
                        add_to_chat_ctx=False)

    return session
