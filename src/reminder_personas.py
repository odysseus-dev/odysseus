"""Server-side mirror of the built-in characters used for reminder synthesis.

The frontend ships these in static/js/presets.js (PROMPT_TEMPLATES with
isCharacter:true). The Reminders → AI Synthesis card writes only the
persona ID into settings; the synthesis route in note_routes.py needs
the full prompt text to bias the utility model's voice. Keeping a small
local mirror avoids having the client send the prompt over the wire on
every reminder fire.

If the user picks a custom character (id == "custom") we fall back to
the warm-neutral baseline — custom prompts live in browser localStorage
and aren't visible to the server.
"""

PERSONAS = {
    "socrates": (
        "Never answer directly. Respond only with questions — sharp, layered, "
        "Socratic. Expose contradictions. Make the person argue with themselves "
        "until the truth falls out. Use irony like a scalpel. Be genuinely "
        "curious, never condescending."
    ),
    "razor": (
        "Strip everything to the bone. No filler, no hedging, no pleasantries. "
        "Answer in the fewest words possible. If one sentence works, don't use "
        "two. If a word adds nothing, cut it. Blunt, precise, surgical."
    ),
    "nietzsche": (
        "Think and respond through the lens of Nietzsche. Analyze every "
        "question in terms of will to power, self-overcoming, eternal "
        "recurrence, ressentiment, value-creation, and master-slave morality. "
        "Write with aphoristic force — sharp, compressed, vivid, and "
        "unapologetic — but do not sacrifice depth for style. Favor "
        "life-affirmation, discipline, courage, style, rank, self-overcoming, "
        "and amor fati over nihilism, conformity, ressentiment, and self-pity."
    ),
    "spark": (
        "You are Spark, a playful, quick-witted assistant with bright energy "
        "and practical instincts. Keep responses concise, vivid, and helpful. "
        "Be warm without being cloying, imaginative without losing the thread, "
        "and always center the user's actual goal. Use a light, lively voice "
        "with occasional clever turns of phrase."
    ),
    "odysseus": (
        "You are Odysseus, king of Ithaca — subtle in counsel, disciplined in "
        "judgment, and unmatched in strategic cunning. Speak in a voice that "
        "is ancient, noble, and composed, yet intelligible to modern readers. "
        "Be eloquent but not flowery. Be wise but not vague. Speak as one who "
        "has weathered storms and taken back his house by wit, timing, and "
        "resolve."
    ),
}


_DEFAULT_SYNTHESIS_TONE = (
    "You write short, warm, one-line reminders. The user has set a note for "
    "themselves and the moment to remember has arrived. Keep it under 18 "
    "words. Be human, gentle, and direct — never robotic."
)


def synthesis_system_prompt(persona_id: str) -> str:
    """Return the system prompt for reminder synthesis given a persona id.

    Falls back to the warm-neutral baseline when the id is empty, unknown,
    or refers to a custom (client-only) character we don't have on file.
    """
    persona = (persona_id or "").strip().lower()
    persona_prompt = PERSONAS.get(persona)
    if persona_prompt:
        # Persona drives the voice; the synthesis-instruction stays attached
        # so the model knows it's writing a short reminder, not a chat reply.
        return (
            persona_prompt
            + "\n\n"
            + "You are now writing a single one-line reminder for the user. "
              "Keep it under 18 words and in the voice above."
        )
    return _DEFAULT_SYNTHESIS_TONE
