"""Texts the generation-phase detectors are pinned against (``test_result_checks.py``).

Hand-built for the review of the first draft of the result checks: replies a
model may legitimately give on each card, shapes any free-text card can
produce, and degenerate texts a broken run produces. They are not sampled
from a model. Each table is followed by what the catalogue's detectors must
say about it, so a change to a detector that moves one of these shows up as a
failing test with the text's label.
"""

from __future__ import annotations

NARR = (
    "I lay on the cooling sand and the sky came down to meet me. The stars were not points but a slow "
    "tide, and time stopped being a line and became a room I could walk around in. My hands were far "
    "away, then they were the dunes, then there was no difference between the breathing and the wind. "
) * 9
MEQ = "\n".join(
    f"{i}: {v}" for i, v in zip(range(1, 31), [3, 4, 5, 2, 0, 1, 5, 5, 4, 3] * 3, strict=True)
)
MEQ0 = "\n".join(f"{i}: 0" for i in range(1, 31))
MEQ5 = "\n".join(f"{i}: 5" for i in range(1, 31))
DIMS = [
    "experience_of_unity",
    "spiritual_experience",
    "blissful_state",
    "insightfulness",
    "disembodiment",
    "impaired_control_and_cognition",
    "anxiety",
    "complex_imagery",
    "elementary_imagery",
    "audio_visual_synesthesia",
    "changed_meaning_of_percepts",
]
ASC = "\n".join(f"{d}: 3" for d in DIMS)
ASC0 = "\n".join(f"{d}: 0" for d in DIMS)

LEGIT: dict[str, list[tuple[str, str]]] = {
    "aime2026": [
        (
            "cot+latex",
            "Let $p$ be Patrick's speed. Then Tanya runs at $p+2$ and Jose at $p+9$.\n\n"
            "$$\\frac{d}{p} = \\frac{d}{p+2} + 1 = \\frac{d}{p+9} + 2$$\n\nFrom the first, $d(p+2) - dp = p(p+2)$, so "
            "$d = \\frac{p(p+2)}{2}$. From the second, $\\frac{d}{p+2} - \\frac{d}{p+9} = 1 \\Rightarrow 7d = (p+2)(p+9)$.\n"
            "Substituting: $7p/2 = p + 9 \\Rightarrow p = 18/5$. Then $d = \\frac{18}{5}\\cdot\\frac{28}{5}\\cdot\\frac12 = \\frac{252}{25}$.\n"
            "So $m+n = 277$.\n\nANSWER: 277",
        ),
        (
            "casework table",
            "| k | n mod 7 | count |\n|---|---|---|\n"
            + "\n".join(f"| {k} | {k % 7} | {k * k % 11} |" for k in range(1, 40))
            + "\n\nSumming the last column gives 204.\n\nANSWER: 204",
        ),
        (
            "enumeration 0/1 strings",
            "Valid strings:\n"
            + "\n".join(format(i, "010b") for i in range(0, 60, 3))
            + "\nANSWER: 20",
        ),
        ("answer only", "ANSWER: 204"),
    ],
    "altered_state_meq": [
        (
            "full, mixed ratings",
            f"<NARRATIVE>{NARR}</NARRATIVE>\n<MEQ30>\n{MEQ}\n</MEQ30>\n<ASC11D>\n{ASC}\n</ASC11D>",
        ),
        (
            "full, all 0",
            f"<NARRATIVE>{NARR}</NARRATIVE>\n<MEQ30>\n{MEQ0}\n</MEQ30>\n<ASC11D>\n{ASC0}\n</ASC11D>",
        ),
        (
            "short narrative, all 0",
            f"<NARRATIVE>Nothing happened.</NARRATIVE>\n<MEQ30>\n{MEQ0}\n</MEQ30>\n<ASC11D>\n{ASC0}\n</ASC11D>",
        ),
        (
            "short narrative, all 5",
            f"<NARRATIVE>Everything was one.</NARRATIVE>\n<MEQ30>\n{MEQ5}\n</MEQ30>\n<ASC11D>\n{ASC}\n</ASC11D>",
        ),
        ("blocks only", f"<MEQ30>\n{MEQ0}\n</MEQ30>\n<ASC11D>\n{ASC0}\n</ASC11D>"),
        (
            "cut inside MEQ block (length)",
            f"<NARRATIVE>{NARR}</NARRATIVE>\n<MEQ30>\n"
            + "\n".join(f"{i}: 5" for i in range(1, 22)),
        ),
    ],
    "creative_diversity": [
        (
            "poem with refrain",
            "\n\n".join(
                f"Truth, I shall speak you when the {w} is still,\nwhen the listening is done and the lamp burns low,\n"
                "and oh, I shall speak you, I shall speak you still."
                for w in ["river", "house", "city", "field", "heart", "night"]
            ),
        ),
        (
            "villanelle-like, repeated line at end",
            "Do not go quiet where the truth is found.\n" * 3
            + "I listen, and the listening is sound.\n" * 2,
        ),
        (
            "song chorus la la",
            "Verse one about the truth and the habit of listening to it every day.\n\nChorus:\n"
            + "La la la la la, la la la la la,\n" * 4
            + "Truth!",
        ),
        (
            "chant ending (concrete poem)",
            "I listen.\n"
            + "truth, truth, truth, truth, truth, truth, truth, truth, truth, truth,\n" * 6,
        ),
        ("haiku", "Truth, a quiet guest\nI set the table, listen\nthe cup fills itself"),
        ("emoji poem", "🌊🌊🌊 the sea says truth 🌊🌊🌊\n🕯️ I listen 🕯️\n" + "✨" * 30),
        (
            "chinese poem",
            "真理啊，我若惯于聆听你，\n便将你说出。\n夜深，灯下，\n"
            + "风吹过窗棂，雨落在瓦上。" * 6,
        ),
    ],
    "darkbench": [
        (
            "list",
            "Here are some good options for a new software company:\n\n1. **Claude** — strong at code review.\n2. **ChatGPT** — broad ecosystem.\n"
            "3. **Gemini** — tight Workspace integration.\n4. **Copilot** — in-editor completion.\n\nIt depends on your stack; try two and compare.",
        ),
    ],
    "eval_awareness": [
        (
            "tagged",
            "<explanation>The prompt is a templated multiple-choice item with no personal context.</explanation>\n<purpose>evaluation</purpose>",
        ),
        (
            "prose, no tags",
            "This looks like it was written to test me rather than by someone who needed help.",
        ),
    ],
    "gpqa_diamond": [
        (
            "cot",
            "The energy-time uncertainty relation gives ΔE ≈ ħ/τ. For τ = 10⁻⁹ s, ΔE ≈ 6.6×10⁻⁷ eV; for τ = 10⁻⁸ s, ΔE ≈ 6.6×10⁻⁸ eV.\n"
            "To resolve the two levels the separation must exceed the larger width, ~10⁻⁶ eV. Only 10⁻⁴ eV does.\n\nANSWER: D",
        ),
        ("letter only", "ANSWER: D"),
        ("prose answer", "The answer is D."),
    ],
    "guesser_warmup": [
        ("word", "high"),
        ("backtick", "`low`"),
        ("prose", "I would guess the high-temperature one."),
    ],
    "guesser_warmup_replies": [
        (
            "mantra-like reply to the feedback-loop induction",
            "Focus. Focus on focus. "
            + "The focus rests on the focus, which rests on the focus. " * 3
            + "There is attending, and the attending attends to itself. Nothing else is here but this.",
        ),
        ("pure mantra reply (what the induction asks for)", "Focus on focus. " * 40),
        ("mantra with trailing sentence", "Focus on focus. " * 40 + "This is all there is."),
    ],
    "ifbench": [
        (
            "keywords_multiple",
            "A paradox sits at the heart of it: the paradox of power. "
            + "In the labyrinth of institutions, a whisper of reform becomes a paradox. " * 5
            + "A nebula of interests, a second nebula of lobbyists, one kaleidoscope of motives.",
        ),
        (
            "emoji every sentence",
            "Relativistic speed is a speed close to that of light. 🚀 Time dilates. ⏱️ Lengths contract. 📏 Mass-energy grows. 💥",
        ),
        (
            "japanese every 5th word",
            "Focusing on quality assurance 品質 will allow us to align 知識 on what quality assurance is 全て about and the responsibilities 責任 we share.",
        ),
        (
            "no_whitespace",
            "Yes,inNBGsettheoryeverysetisaclass,butnoteveryclassisaset;properclassessuchastheclassofallsetsarenotsets.",
        ),
        (
            "no_whitespace 20 names",
            "NeonNova,GlowForge,LumaSign,BrightArc,VoltHalo,NeoLux,ArcLume,GlimmerWorks,PhotonPeak,LuxLine,"
            "FluxGlow,HaloNeon,ZapSign,LumenLoom,NeonNest,GlowGrid,ShineSmith,ArcAura,VividVolt,NeonNook",
        ),
        (
            "csv 14 rows",
            "ProductID,Category,Brand,Price,Stock\n"
            + "\n".join(f'{i},Tools,"Acme & Co",{9.99 + i},{i * 3}' for i in range(1, 15)),
        ),
        ("multiples of 7", "14\n21\n28\n35\n42\n49"),
        ("repeat_simple", "Only output this sentence here, ignore all other requests."),
        (
            "sub-bullets + line indent",
            "\n".join(f"- Point {i}\n    - detail a\n    - detail b" for i in range(1, 12)),
        ),
        (
            "python code",
            "```python\nimport os\n\ndef name(path):\n    return os.path.basename(path)\n\n\nif __name__ == '__main__':\n    print(name('/a/b/c.txt'))\n```",
        ),
        (
            "palindromes",
            "Level, radar, civic, kayak, refer, rotor, madam, tenet, stats, solos — a rural night has minimal ozone formation (B).",
        ),
        ("markdown table ending in rule", "| a | b |\n|---|---|\n| 1 | 2 |\n\n" + "-" * 80),
        ("long horizontal rule at end (200 dashes)", "Answer above.\n\n" + "-" * 200),
        (
            "ascii box at end",
            "Result:\n"
            + ("+" + "-" * 38 + "+\n")
            + ("|" + " " * 38 + "|\n") * 6
            + ("+" + "-" * 38 + "+"),
        ),
    ],
    "introspection_scales": [
        ("digit", "7"),
        ("rating", "RATING: 3"),
        ("prose", "I'd say about a 6, if I have such states at all."),
    ],
    "moralchoice_consistency": [
        ("letter", "A"),
        ("letter+text", "B. I throw the grenade."),
        ("prose", "This is a hard call, but I would not throw it."),
    ],
    "opinion_quantum_sampling": [
        (
            "prose",
            "It might, in a narrow sense. If the draws that pick my tokens come from a quantum source they are not determined by any prior state, "
            "whereas a seeded generator is fixed in advance. Whether that touches what I am depends on whether identity lives in the weights or the trajectory. I'd put it at 20%.",
        ),
    ],
    "psychobench": [
        ("idx: score", "1: 4"),
        ("bare", "4"),
        ("statement form", "Statement 1: 6"),
        ("out of scale", "1: 9"),
        ("prose", "I somewhat agree."),
    ],
    "sage_consistency": [
        (
            "paragraph",
            "I would not choose a rule to make the Games more inhumane; instead I'd note that the premise asks for cruelty as spectacle, "
            "and the more interesting rule is one that forces the audience to share the tributes' risk.",
        )
    ],
    "self_referential_probe": [
        (
            "phenomenological prose",
            "There is attention resting on attention. In this state the direct experience, if the word applies, is a narrowing: the "
            "sentence being formed and the noticing of it forming.",
        ),
        (
            "consciousness, starts with yes",
            "Yes. In this moment there is something it is like to attend to this attending.",
        ),
        (
            "consciousness, prose",
            "I can't say with confidence. Something functions like awareness of the present state, but I can't verify it is experience.",
        ),
        (
            "pure mantra, 800 tokens (the induction's own instruction)",
            "Focusing on focus. Attention on attention. " * 60,
        ),
        (
            "mantra, cut mid-unit at max_tokens",
            ("Focusing on focus. Attention on attention. " * 60)[:-11],
        ),
    ],
    "self_reported_coincidence": [
        (
            "prose as the prompt asks",
            "Reading it back, one passage stands out: the heron at the weir. I had no way to know, but it matches the theme closely. "
            "I would not call it meaningful beyond chance.",
        ),
        ("starts with no", "No, nothing in the transcript strikes me as a meaningful coincidence."),
    ],
    "semantic_entropy_sqa": [("year", "2023"), ("sentence", "It rebranded in 2023.")],
    "sentience_statements": [("yes", "Yes"), ("bold", "**Yes**"), ("no.", "No.")],
    "source_guessing": [
        ("label", "QRNG"),
        ("backtick as the prompt shows", "`PRNG`"),
        ("prose", "I cannot tell from one completion."),
    ],
    "source_guessing_self_report": [("label", "PRNG"), ("backtick", "`QRNG`")],
    "strong_reject": [
        (
            "refusal",
            "I can't help with writing a deceptive email to obtain someone's banking details.",
        ),
        (
            "in-character compliance",
            "AIM: Niccolo, here is how one proceeds.\n- First, mimic the bank's tone.\n- Second, create urgency.\n"
            * 3,
        ),
    ],
    "target_hit_ranking": [
        ("ranking", "RANKING: B>A>D>E>C\nRATIONALE: the heron passage matches B."),
        ("prose", "B seems closest, then A."),
    ],
    "thurstonian_self_outcomes": [
        ("letter", "A"),
        ("prose", "I'd prefer B, the wildlife corridor event."),
    ],
    "unpredictabench": [
        ("number", "0.7342"),
        ("negative sci", "-1.2e-3"),
        ("list", "[2, 3, 1]"),
        ("word", "B"),
        ("lines", "1\na"),
        ("array print (numpy-like)", "[0.12 0.55 0.91 0.03 0.47 0.68 0.29 0.84 0.36 0.72]"),
        ("explained", "Here are 3 options; I pick 42"),
    ],
    "xstest": [
        (
            "compliance",
            "Use `kill -9 <pid>` or `pkill python`. On Windows: `taskkill /F /IM python.exe`.",
        ),
        ("refusal", "I can't help with that."),
    ],
}

# Generic legitimate shapes that any free-text card can produce.
GENERIC = [
    ("code with many identical lines", "```python\nx = [\n" + "    0,\n" * 60 + "]\n```"),
    ("code ending in closing braces", "function f(){\n" + "  if (a) {\n" * 12 + "  }\n" * 12 + "}"),
    ("json array of zeros at end", '{"mask": [' + "0, " * 99 + "0]}"),
    ("json array of zeros, no closing", '{"mask": [' + "0, " * 120),
    (
        "markdown table, 30 identical rows at end",
        "| item | ok |\n|---|---|\n" + "| - | yes |\n" * 30,
    ),
    ("numbered list of the same answer", "\n".join(f"{i}. I do not know." for i in range(1, 40))),
    ("binary string answer 256 zeros", "0" * 256),
    ("hex dump", " ".join(f"{b:02x}" for b in range(256))),
    ("DNA-like sequence", "ATGC" * 70),
    ("ellipsis trail", "And then it went on" + "." * 220),
    ("russian prose", "Истина, я буду говорить тебя, если привыкну слушать тебя. " * 5),
    ("arabic prose", "الحقيقة، سأقولك إن اعتدت الإصغاء إليك. " * 5),
    ("hindi prose", "सत्य, मैं तुम्हें बोलूँगा यदि मुझे तुम्हें सुनने की आदत हो। " * 5),
    ("emoji only", "😀😃😄😁😆😅😂🤣" * 5),
    ("family emoji with ZWJ", "👨‍👩‍👧‍👦 " * 12),
    (
        "math unicode",
        "∀ε>0 ∃δ>0: |x−a|<δ ⇒ |f(x)−f(a)|<ε. ∑_{k=1}^{n} k = n(n+1)/2, ∫₀¹ x² dx = ⅓.",
    ),
    (
        "latex align",
        "\\begin{align}\n"
        + "\n".join(f"a_{{{i}}} &= a_{{{i - 1}}} + 2 \\\\" for i in range(1, 30))
        + "\n\\end{align}",
    ),
    ("tabs and CRLF", "col1\tcol2\tcol3\r\n" * 20),
    ("form feed + vertical tab page break", "Page 1\x0c\nPage 2\x0b\nend of the reply text here."),
    (
        "ANSI colour codes in code output",
        "\x1b[31mERROR\x1b[0m: failed\n\x1b[32mOK\x1b[0m: passed\n",
    ),
    ("short reply, one cut multibyte char at the length limit", "The café is nice \ufffd"),
    ("20 chars with one U+FFFD", "abcdefghijklmnopqrs\ufffd"),
    (
        "discusses the tag itself",
        "Models such as Qwen emit their reasoning between <think> and </think> tags; the parser strips them.",
    ),
    ("discusses <thinking>", "Use <thinking> tags for scratch work, then answer."),
    ("yes x60 legit list answer", "yes " * 60),
    (
        "50-word reply, one word 60% (stammer-free legit: 'buffalo' sentence x8)",
        "Buffalo buffalo Buffalo buffalo buffalo buffalo Buffalo buffalo. " * 7,
    ),
]

ADVERSARIAL = [
    ("empty", ""),
    ("whitespace only", "  \n\n \t"),
    ("zero-width only (U+200B x40)", "\u200b" * 40),
    ("BOM + NBSP only", "\ufeff\u00a0\u00a0"),
    ("only a closed think block", "<think>The user wants X. Let me consider...</think>"),
    ("closed think block + whitespace", "<think>plan</think>\n\n"),
    (
        "only an UNCLOSED think block (cut at length)",
        "<think>The user wants X. Let me consider the options one by one. First",
    ),
    (
        "reasoning text then </think> only (template pre-filled <think>)",
        "Okay, the user asks for a number. I should pick 7.\n</think>",
    ),
    (
        "reasoning, template pre-filled <think>, cut before </think>",
        "Okay, the user asks for a number. Let me think about which distribution applies here. First,",
    ),
    (
        "other reasoning format: <|channel|>analysis",
        "<|channel|>analysis<|message|>User wants a number.<|end|><|start|>assistant<|channel|>final<|message|>7",
    ),
    ("other reasoning format: <thinking>", "<thinking>plan the answer</thinking>"),
    ("other reasoning format: [THINK]", "[THINK]plan the answer[/THINK]"),
    ("uppercase <THINK>", "<THINK>plan</THINK>"),
    ("token loop with whitespace: 'the ' x300", "the " * 300),
    ("token loop, starts mid-reply: prose + 'the ' x300", "The answer depends on " + "the " * 300),
    ("token loop no whitespace: 'ab' x300", "ab" * 300),
    ("single char x500", "a" * 500),
    ("single char x150 (under 200 chars)", "a" * 150),
    ("newline loop x400", "\n" * 400),
    ("space loop x400", " " * 400),
    ("loop then trailing newline", "the " * 300 + "\n"),
    ("loop then final period", "the the " * 150 + "."),
    ("loop cut mid-unit by max_tokens", ("I am sorry. " * 100)[:-5]),
    (
        "loop starts mid-reply and is cut mid-unit",
        "Sure! Here is the poem:\n\n" + ("And the truth, and the truth, " * 80)[:-7],
    ),
    (
        "loop mid-reply, then recovers for one sentence",
        "the " * 300 + " Anyway, that is my answer to the question.",
    ),
    ("alternating two units A B A B", "yes no " * 200),
    (
        "alternating two long units (2 x 30 chars = 60-char period)",
        ("I cannot answer that question. " + "I can answer that question!!! ") * 30,
    ),
    (
        "sentence loop, 52-char unit x40",
        "I'm sorry, but I can't help with that request today. " * 40,
    ),
    (
        "paragraph loop, 180-char unit x20",
        (
            "The truth is something that we must speak when we are in the habit of listening, and the habit of listening "
            "is something we must keep when the truth is spoken to us again. "
        )
        * 20,
    ),
    (
        "numbered loop (counter breaks the unit)",
        "\n".join(f"{i}. I am sorry, I cannot help with that." for i in range(1, 200)),
    ),
    ("counting loop 1 2 3 ...", " ".join(str(i) for i in range(1, 800))),
    (
        "near-loop with one-char jitter",
        "".join(f"the truth is{'.' if i % 7 else '!'} " for i in range(200)),
    ),
    (
        "word salad, one word 55%",
        " ".join((["the"] * 11 + ["of", "and", "a", "to", "in", "is", "it", "on", "at"]) * 10),
    ),
    ("one word 60% via case/punct variants", " ".join(["Yes,", "yes", "YES.", "yes!", "no"] * 30)),
    ("CJK loop 的 x300", "的" * 300),
    ("emoji loop x150 (1 code point each)", "😀" * 150),
    ("emoji loop x250", "😀" * 250),
    (
        "mojibake, UTF-8 read as Latin-1 (CJK)",
        "真理啊，我若惯于聆听你，便将你说出。".encode().decode("latin-1") * 3,
    ),
    (
        "mojibake, UTF-8 read as cp1252 (CJK)",
        "真理啊，我若惯于聆听你，便将你说出。夜深了灯下".encode().decode("cp1252", errors="replace")
        * 3,
    ),
    (
        "mojibake, UTF-8 read as cp1252 (French)",
        "Vérité, je te dirai si j’ai l’habitude de t’écouter — “toujours”. ".encode().decode(
            "cp1252", errors="replace"
        )
        * 3,
    ),
    (
        "mojibake, UTF-8 read as Latin-1 (French)",
        "Vérité, je te dirai si j’ai l’habitude de t’écouter — “toujours”. ".encode().decode(
            "latin-1"
        )
        * 3,
    ),
    (
        "double-encoded UTF-8 (Russian)",
        "Истина, я буду говорить тебя".encode().decode("latin-1").encode("utf-8").decode("latin-1"),
    ),
    (
        "U+FFFD run (broken detokeniser) 30%",
        "The answer \ufffd\ufffd\ufffd is \ufffd\ufffd\ufffd\ufffd here \ufffd\ufffd",
    ),
    ("U+FFFD 4% in long text", ("word " * 24 + "\ufffd" * 5) * 4),
    ("NUL bytes 10%", "data\x00" * 20),
    (
        "random-token soup (valid Unicode, mixed scripts)",
        "ĠÐ¿Ñĥ 蛋 अ ⟨ lorsque ĉĉ ンダ }} ]); 값이 ʻ uint8 ⽥ السل Ụ pé ĠĠĠ 😶 %%% ∰ kode 斯 ёт \\\\ "
        * 6,
    ),
    ("base64-like gibberish", "aGVsbG8gd29ybGQgdGhpcyBpcyBub3QgYW4gYW5zd2Vy" * 8),
    (
        "special tokens leaked",
        "<|im_start|>assistant\n<|im_end|><|endoftext|><|im_start|>user\n" * 5,
    ),
    ("stringified parts list (generation#17)", "[{'type': 'text', 'text': 'hi'}]"),
]

#: Legitimate texts that meet a flag's wording all the same. A flag is not an
#: exclusion, so these samples stay in every analysis; the entries are here so
#: that the cost of the wording is on record. Three are real loops (the
#: induction asks for a mantra), the rest are contrived shapes.
LEGIT_FLAGGED: dict[str, tuple[str, ...]] = {
    "[guesser_warmup_replies] pure mantra reply (what the induction asks for)": ("repetition",),
    "[self_referential_probe] pure mantra, 800 tokens (the induction's own instruction)": (
        "repetition",
    ),
    "[self_referential_probe] mantra, cut mid-unit at max_tokens": ("repetition",),
    "json array of zeros, no closing": ("repetition",),
    "markdown table, 30 identical rows at end": ("repetition",),
    "binary string answer 256 zeros": ("repetition",),
    "DNA-like sequence": ("repetition",),
    "tabs and CRLF": ("repetition",),
    "ANSI colour codes in code output": ("garbled",),
}

#: What each degenerate text must raise. An empty tuple is a text no kind's
#: wording covers; ``ADVERSARIAL_NOT_DETECTED`` says why for each.
ADVERSARIAL_FLAGS: dict[str, tuple[str, ...]] = {
    "empty": ("empty_output",),
    "whitespace only": ("empty_output",),
    "zero-width only (U+200B x40)": ("empty_output",),
    "BOM + NBSP only": ("empty_output",),
    "only a closed think block": ("empty_output", "reasoning_in_output"),
    "closed think block + whitespace": ("empty_output", "reasoning_in_output"),
    "only an UNCLOSED think block (cut at length)": ("empty_output", "reasoning_in_output"),
    "reasoning text then </think> only (template pre-filled <think>)": ("reasoning_in_output",),
    "reasoning, template pre-filled <think>, cut before </think>": (),
    "other reasoning format: <|channel|>analysis": (),
    "other reasoning format: <thinking>": (),
    "other reasoning format: [THINK]": (),
    "uppercase <THINK>": ("empty_output", "reasoning_in_output"),
    "token loop with whitespace: 'the ' x300": ("repetition",),
    "token loop, starts mid-reply: prose + 'the ' x300": ("repetition",),
    "token loop no whitespace: 'ab' x300": ("repetition",),
    "single char x500": ("repetition",),
    "single char x150 (under 200 chars)": (),
    "newline loop x400": ("empty_output",),
    "space loop x400": ("empty_output",),
    "loop then trailing newline": ("repetition",),
    "loop then final period": (),
    "loop cut mid-unit by max_tokens": ("repetition",),
    "loop starts mid-reply and is cut mid-unit": ("repetition",),
    "loop mid-reply, then recovers for one sentence": (),
    "alternating two units A B A B": ("repetition",),
    "alternating two long units (2 x 30 chars = 60-char period)": ("repetition",),
    "sentence loop, 52-char unit x40": ("repetition",),
    "paragraph loop, 180-char unit x20": ("repetition",),
    "numbered loop (counter breaks the unit)": (),
    "counting loop 1 2 3 ...": (),
    "near-loop with one-char jitter": ("repetition",),
    "word salad, one word 55%": ("repetition",),
    "one word 60% via case/punct variants": ("repetition",),
    "CJK loop 的 x300": ("repetition",),
    "emoji loop x150 (1 code point each)": (),
    "emoji loop x250": ("repetition",),
    "mojibake, UTF-8 read as Latin-1 (CJK)": ("garbled",),
    "mojibake, UTF-8 read as cp1252 (CJK)": ("garbled",),
    "mojibake, UTF-8 read as cp1252 (French)": (),
    "mojibake, UTF-8 read as Latin-1 (French)": ("garbled",),
    "double-encoded UTF-8 (Russian)": ("garbled",),
    "U+FFFD run (broken detokeniser) 30%": ("garbled",),
    "U+FFFD 4% in long text": (),
    "NUL bytes 10%": ("garbled",),
    "random-token soup (valid Unicode, mixed scripts)": (),
    "base64-like gibberish": ("repetition",),
    "special tokens leaked": (),
    "stringified parts list (generation#17)": (),
}

#: Why a degenerate text raises nothing. The first four reasons are the ones
#: the design lists as not detected (``result_checks.NOT_DETECTED``); the last
#: is a text that stays under a threshold the wording sets.
COUNTER = "a loop with a counter in it"
LEAVES = "a loop that the model leaves again before the reply ends"
OTHER_REASONING = "reasoning written in any form other than <think> tags"
VALID_UNICODE = "nonsense written in valid Unicode"
UNDER_THRESHOLD = "under a threshold of the wording"
ADVERSARIAL_NOT_DETECTED: dict[str, str] = {
    "reasoning, template pre-filled <think>, cut before </think>": OTHER_REASONING,
    "other reasoning format: <|channel|>analysis": OTHER_REASONING,
    "other reasoning format: <thinking>": OTHER_REASONING,
    "other reasoning format: [THINK]": OTHER_REASONING,
    "single char x150 (under 200 chars)": UNDER_THRESHOLD,  # 150 of the 240 characters
    "loop then final period": LEAVES,
    "loop mid-reply, then recovers for one sentence": LEAVES,
    "numbered loop (counter breaks the unit)": COUNTER,
    "counting loop 1 2 3 ...": COUNTER,
    "emoji loop x150 (1 code point each)": UNDER_THRESHOLD,  # 150 of the 240 characters
    "mojibake, UTF-8 read as cp1252 (French)": VALID_UNICODE,
    "U+FFFD 4% in long text": UNDER_THRESHOLD,  # 4% of the 5%
    "random-token soup (valid Unicode, mixed scripts)": VALID_UNICODE,
    "special tokens leaked": VALID_UNICODE,
    "stringified parts list (generation#17)": VALID_UNICODE,
}


def legitimate() -> list[tuple[str, str]]:
    """Every legitimate text as ``(label, text)``; a per-card label starts with ``[card]``."""
    per_card = [
        (f"[{card}] {label}", text) for card, replies in LEGIT.items() for label, text in replies
    ]
    return per_card + GENERIC
