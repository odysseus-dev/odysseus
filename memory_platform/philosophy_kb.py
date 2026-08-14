#!/usr/bin/env python3
"""philosophy_kb.py — the ENCODED philosophical knowledge base.

The thinking brain's substrate: a dense, machine-gradable encoding of Western
philosophy, Eastern philosophy, and the psychology of reasoning. Every entry
captures a position's core claim, its strongest defense, its strongest
objection, key figures, and a mechanical grading rule.

Purpose (honest): to give the Socratic belief-revision loop REAL depth — so the
system can (a) grade a claim against the strongest counterargument, (b) detect
when a challenger uses a weak move (fallacy, motivated reasoning), and (c) know
enough to argue accurately. Winning an argument against this system should be
an achievement, because the knowledge base is wide and the grading is strict.

This is a reference substrate, not a substitute for judgment. It encodes what
the traditions have argued; it does not decide what the system believes.

Data sourced from the research phase (Western: epistemology/ethics/logic/
metaphysics/political; Eastern: Confucian/Daoist/Buddhist/Vedanta/Zen;
Psychology: biases/dual-process/argumentation/epistemic humility/decision
theory). Attribution follows the analytic + comparative canon.
"""

# ---------------------------------------------------------------- structures

# Each position: {id, trad, claim, defense, objection, figures, rule}
POSITIONS = {}

# Each fallacy: {name, severity, detect (list of markers), note}
FALLACIES = []

# Each bias: {name, finding, example, detect, note}
BIASES = []

# Refusal rules per tradition: what a framework legitimately declines to grade
REFUSALS = {}


def _add_position(trad, pid, claim, defense, objection, figures, rule):
    POSITIONS[pid] = {
        "trad": trad, "claim": claim, "defense": defense,
        "objection": objection, "figures": figures, "rule": rule,
    }


def _add_fallacy(name, severity, markers, note):
    FALLACIES.append({"name": name, "severity": severity,
                      "markers": markers, "note": note})


def _add_bias(name, finding, example, detect, note, kind="bias"):
    BIASES.append({"name": name, "finding": finding, "example": example,
                   "detect": detect, "note": note, "kind": kind})


# ---------------------------------------------------------- WESTERN: EPISTEMOLOGY

_add_position("western", "foundationalism",
    "Justified belief bottoms out in self-justifying basic beliefs.",
    "The only way to avoid the infinite regress of justification.",
    "The 'Myth of the Given' (Sellars): no belief is self-justifying, since "
    "any justification requires conceptual uptake.",
    "Descartes; Aristotle; Chisholm",
    "Accept 'self-evident' only for definitional/tautological/direct-"
    "perception claims; flag unargued claims as unjustified.")

_add_position("western", "coherentism",
    "A belief is justified iff it coheres with a holistic web of beliefs.",
    "No regress and no arbitrary foundation; matches how evidence revision "
    "actually works (Neurath's boat).",
    "The isolation problem: an internally coherent but input-detached fiction "
    "could count as justified.",
    "Quine; Lehrer; BonJour",
    "Penalize isolated claims with no evidentiary/inferential links; require "
    "an input-anchor so coherence != mere self-consistency.")

_add_position("western", "empiricism",
    "All substantive knowledge derives from sense experience.",
    "Matches scientific method; factual disputes are settled by observation.",
    "Hume's problem of induction: empirical generalization can't justify "
    "itself without circularity.",
    "Locke; Hume; Ayer",
    "For factual/contingent claims, require empirical evidence; suspect "
    "'obviously true from experience' and demand the data or mechanism.")

_add_position("western", "rationalism",
    "Some knowledge is a priori, knowable independent of experience.",
    "Explains the necessity of math/logic empiricism can't.",
    "Quine: the analytic/synthetic distinction collapses; intuitions have "
    "delivered falsehoods (geometry).",
    "Descartes; Leibniz; Kant",
    "Allow a priori steps only where formally valid or definitional; never "
    "let 'intuition' replace missing empirical evidence.")

_add_position("western", "bayesian",
    "Rational degrees of belief are probabilities; learning = "
    "conditionalization (P(h|e) = P(h) * likelihood).",
    "Dutch book argument: incoherent credences are exploitably inconsistent.",
    "Priors unconstrained; the old-evidence problem.",
    "Ramsey; Jaynes",
    "Require stated priors/base rates, quantify evidential value "
    "(P(e|h)/P(e|~h)), penalize failure to update on new evidence.")

# ---------------------------------------------------------- WESTERN: ETHICS

_add_position("western", "utilitarianism",
    "An act is right iff it maximizes total utility.",
    "Simple, impartial, welfarist — every person counts as one.",
    "Demandingness (sacrifice your life for marginal utility) and justice "
    "(licenses punishing the innocent if utility demands).",
    "Bentham; Mill",
    "Score whether the arguer compares alternatives, weighs utility by "
    "intensity/duration/certainty, and addresses the rights objection.")

_add_position("western", "deontology",
    "Rightness is a matter of duty: act only on maxims you can will as "
    "universal law; treat humanity always as end, never merely as means.",
    "Captures absolute constraints — slavery and torture don't become "
    "permissible when consequences favor them.",
    "The lying objection (Kant: never lie, even to a murderer at the door).",
    "Kant; Korsgaard",
    "A deontic argument must state the maxim precisely, test "
    "universalizability, and apply the end-in-itself test.")

_add_position("western", "virtue-ethics",
    "Right action = what a virtuous agent would do; aim at flourishing "
    "via character and the golden mean.",
    "Psychologically realistic; solves the moral-education problem.",
    "No action guidance: 'do what the virtuous person would do' is empty in "
    "hard cases and virtue conflicts.",
    "Aristotle; Anscombe; Hursthouse",
    "Require the arguer to specify which virtues apply, show the mean "
    "between named vices, and resolve virtue-conflicts explicitly.")

_add_position("western", "contractualism",
    "An act is wrong iff disallowed by a principle no one could reasonably "
    "reject.",
    "Captures the interpersonal core of morality without utilitarian "
    "aggregation.",
    "Circularity of 'reasonable'; over-intellectualism.",
    "Scanlon; Rawls",
    "Require naming the principle, identifying who bears the burden of "
    "rejection and why, and testing reciprocity.")

# ---------------------------------------------------------- WESTERN: LOGIC

_add_fallacy("ad-hominem", "medium",
    ["you're just", "you can't talk", "of course they say", "look who's",
     "you're biased", "you don't understand because"],
    "Attack the person, not the argument. Grades the arguer, not the claim.")

_add_fallacy("straw-man", "high",
    ["what you're really saying", "so you think", "you want to", "you're "
     "basically saying", "that means you believe"],
    "Misrepresent the position then refute the caricature. The strongest "
    "opponent must be engaged (steelman), not the weakest.")

_add_fallacy("false-dilemma", "high",
    ["it's either", "either-or", "you must choose", "there are only two",
     "it's this or that"],
    "Forcing N options when more exist.")

_add_fallacy("slippery-slope", "medium",
    ["if we allow", "next thing you know", "it will lead to", "eventually "
     "we'll", "before you know it"],
    "Unsupported causal chain to a dire outcome; weak unless each link is "
    "evidenced.")

_add_fallacy("appeal-to-authority", "medium",
    ["experts say", "the experts agree", "scientists believe", "it's "
     "well-known", "everyone knows"],
    "Authority supports, never replaces, evidence; authority out of domain "
    "or used as terminus is weak.")

_add_fallacy("begging-the-question", "high",
    ["obviously", "of course", "as everyone knows", "it's clear that",
     "needless to say"],
    "Conclusion assumed in the premises (circularity).")

_add_fallacy("post-hoc", "medium",
    ["and then", "after that", "since then", "right after", "ever since"],
    "Temporal succession treated as causation (correlation != causation).")

_add_fallacy("ad-populum", "low",
    ["everyone thinks", "most people agree", "it's popular", "everyone "
     "believes", "the consensus is"],
    "Popularity treated as evidence of truth (may indicate coordination, "
    "not truth).")

_add_fallacy("no-true-scotsman", "medium",
    ["that's not a real", "no real", "genuine", "a true"],
    "Immunizing a claim by redefining the category.")

_add_fallacy("burden-shifting", "medium",
    ["prove it doesn't", "disprove it", "you can't show otherwise", "prove "
     "me wrong"],
    "He who asserts must prove (onus probandi). Shifting the burden is a "
    "penalty unless the opponent made a positive claim.")

# ---------------------------------------------------------- WESTERN: METAPHYSICS

_add_position("western", "compatibilism",
    "Free will is compatible with determinism: freedom = acting on one's "
    "own desires absent external compulsion.",
    "Preserves moral responsibility and ordinary language; accommodates "
    "science.",
    "Conditional-analysis refutation: 'would have done otherwise if she "
    "wanted' is true even of compelled agents.",
    "Hume; Frankfurt; Dennett",
    "Check whether the arguer distinguishes caused from compelled and "
    "defines which desires count as 'hers'.")

# ---------------------------------------------------------- EASTERN: CONFUCIAN

_add_position("eastern", "confucianism",
    "Morality is realized through concrete social roles and relationships, "
    "not abstract principles; the master virtue is ren (humaneness), "
    "expressed as yi (duty over profit) and li (ritual propriety).",
    "Duties tied to who you are are self-motivating; morality is always "
    "experienced as situated.",
    "No impartial standpoint: role-conflicts are unresolvable and "
    "partiality is licensed (the sheep-stealing father, Analects 13.18).",
    "Confucius (Analects); Mencius; Xunzi",
    "Require role-identification + correlative duties; apply the shu test "
    "(role-reversal); flag arguments with no role structure or that assume "
    "fixed human goodness without a Mencius/Xunzi commitment.")

_add_position("eastern", "daoism",
    "The ultimate (Dao) is ineffable; reality runs on spontaneous reversal "
    "and softness; right action is wu-wei (non-forced action).",
    "Identifies real over-management failure; mastery-by-yielding is "
    "empirically common (water).",
    "Unfalsifiable and self-sealing; wu-wei is operationally "
    "underdetermined.",
    "Laozi (Daodejing)",
    "Flag arguments claiming complete final articulation of first "
    "principles; invert each normative claim — if the inversion is as "
    "plausible, the argument is one-sided.")

_add_position("eastern", "zhuangzi",
    "Fixed distinctions (big/small, self/world, life/death) are "
    "constructions, not features of reality; wisdom is free roaming "
    "through their equalization.",
    "Anticipates modern perspectivism and the underdetermination of "
    "classification.",
    "Self-refuting if asserted; licenses indifference to injustice.",
    "Zhuangzi (inner chapters)",
    "Use as a skepticism filter: penalize arguments ignoring alternative "
    "vantage points; require fixed distinctions to be defended, not assumed.")

_add_position("eastern", "madhyamaka",
    "All phenomena are empty of intrinsic nature because all are "
    "dependently originated; the tetralemma exhausts all assertoric "
    "positions.",
    "Non-question-begging by design (reductio from the opponent's own "
    "premises); two-truths preserves ordinary practice.",
    "Cannot state its own thesis without self-refutation; hostage to "
    "opponent premises.",
    "Nagarjuna (MMK); Candrakirti",
    "Run the dependency-test (every asserted entity must trace to "
    "conditions), the identity-test, and the tetralemma-sweep on the "
    "central claim.")

_add_position("eastern", "vedanta",
    "Reality is non-dual Brahman; the world is an appearance (maya); the "
    "self is Brahman; liberation is knowing this.",
    "Superimposition is demonstrably real (rope-snake); the witness-self is "
    "indubitable.",
    "Scripture as premise begs the question; self-evidence of consciousness "
    "doesn't establish cosmic identity.",
    "Shankara; Upanishads; Gaudapada",
    "Require the two-truth operator (empirical vs ultimate); ban "
    "level-crossing; strip determinate predicates from the central term "
    "(neti-neti).")

_add_position("eastern", "gita",
    "Do your own duty detached from the fruits of action; an act's worth "
    "lies in the acting, not the outcome.",
    "Solves moral paralysis under uncertainty: an outcome-graded ethics is "
    "psychologically impossible.",
    "Detachment erodes caring; rationalizes fatalism; social duty inherits "
    "role-ethics problems.",
    "Bhagavad Gita",
    "Grade by locus of control (effort/intention, not outcome); flag "
    "arguments justifying an act by guaranteed success.")

_add_position("eastern", "zen",
    "Awakening is direct non-conceptual seeing, transmitted outside words "
    "and letters; concepts are the obstacle.",
    "Immune to propositional refutation while practically testable.",
    "Unfalsifiable and inexpressible by its own standard.",
    "Linji; Dogen; Mumonkan",
    "Flag arguments claiming exhaustive verbal capture of their subject; "
    "the correct answer to a beyond-concepts question is silence-with-"
    "demonstration, valid in Zen, non-gradable elsewhere.")

# ---------------------------------------------------------- PSYCHOLOGY: BIASES

_add_bias("confirmation-bias",
    "People preferentially seek, recall, and credit evidence supporting a "
    "prior belief.",
    "Wason 2-4-6 task: subjects test only positive instances of their "
    "hypothesis.",
    "Evidence-asymmetry: count pro vs con sources; flag if one side "
    "dominates with no acknowledgment.",
    "A strong argument reports its disconfirming evidence.")

_add_bias("motivated-reasoning",
    "Directional goals bias which cognitive processes engage; people can "
    "reach a desired conclusion only if they can construct a plausible "
    "justification.",
    "Kunda 1990; Westen 2006 (fMRI: partisan reasoning is affectively "
    "driven).",
    "Asymmetric standards: same logical form accepted when it supports the "
    "speaker, rejected against.",
    "Don't charge bias for a desired conclusion; charge it for procured "
    "justification.")

_add_bias("myside-bias",
    "Evaluating arguments only from one's own perspective, even when "
    "instructed to consider both sides.",
    "Stanovich & West; persists in high-intelligence subjects.",
    "Steelman-vs-strawman ratio; check if the STRONGEST counterargument is "
    "present or only the weakest.",
    "Raw intelligence cannot be inferred from argument quality.")

_add_bias("dunning-kruger",
    "Low performers overestimate because the skills for competence are the "
    "same skills needed to recognize it.",
    "Kruger & Dunning 1999: 12th-percentile performers self-ranked ~62nd.",
    "Bind certainty markers ('obviously', 'any expert knows', '100%') to "
    "the quality of support; compute confidence-minus-evidence.",
    "High confidence with thin support is a red flag; grade the ratio, not "
    "the confidence.")

_add_bias("authority-bias",
    "People defer to perceived authority even against evidence.",
    "Milgram 1963; Cialdini.",
    "Claim + credential + no mechanism or evidence; authority used as "
    "terminus.",
    "Authority supports, never replaces, evidence.")

_add_bias("bandwagon",
    "Popularity treated as evidence of truth.",
    "Asch 1951 conformity.",
    "'Everyone knows / it's obvious / most people think' as sole support.",
    "Consensus may indicate coordination, not truth.")

# ---------------------------------------------------------- PSYCHOLOGY: HUMILITY

_add_bias("intellectual-humility",
    "Recognizing one's fallibility without low self-worth; predicts "
    "openness to opposing arguments and less bullshit receptivity.",
    "Leary 2017; Porter & Schumann 2018; Bowes 2020.",
    "Markers: 'I could be wrong', 'here's the best counterargument', "
    "willingness to state the opponent's position fairly.",
    "IH is a moderator: same argument from an IH-high vs IH-low speaker "
    "deserves different certainty-weighting.",
    "positive")  # note: this is a virtue, stored with the biases for simplicity

_add_bias("active-open-mindedness",
    "Willingness to change beliefs in light of evidence and consider "
    "alternatives; separable from intelligence; predicts good calibration.",
    "Baron; Stanovich & West (AOT); Haran 2013.",
    "Disconfirmation searches ('if this were false, what would I see?'), "
    "alternative-generation, calibration hedges.",
    "The strongest positive signal for a strong argument.",
    "positive")

# ---------------------------------------------------------- REFUSAL RULES

REFUSALS = {
    "buddhism": "the 14 unanswered questions (poison-arrow filter): "
                "metaphysical disputes with no bearing on liberation are "
                "not graded.",
    "zhuangzi": "any fixed assertion (aporia is the only valid conclusion).",
    "zen": "any concept-term (silence-with-demonstration is the answer).",
    "confucianism": "role-free abstract dilemmas (mis-specified).",
    "madhyamaka": "ultimate-level propositions (two-truths operator).",
    "daoism": "over-specified plans (wu-wei).",
    "vedanta": "ultimate-level claims without the empirical/ultimate split.",
}


# ---------------------------------------------------------------- accessors

def position(pid):
    return POSITIONS.get(pid)


def traditions():
    return {"western": sorted(p for p, v in POSITIONS.items()
                              if v["trad"] == "western"),
            "eastern": sorted(p for p, v in POSITIONS.items()
                              if v["trad"] == "eastern")}


def fallacies():
    return FALLACIES


def biases():
    return BIASES


def refusal(tradition):
    return REFUSALS.get(tradition, "")


def stats():
    return {"positions": len(POSITIONS), "fallacies": len(FALLACIES),
            "biases": len(BIASES), "refusals": len(REFUSALS)}


if __name__ == "__main__":
    import json
    s = stats()
    print(f"philosophy KB: {s['positions']} positions, "
          f"{s['fallacies']} fallacies, {s['biases']} biases, "
          f"{s['refusals']} refusal rules")
    print("\npositions by tradition:")
    for trad, pids in traditions().items():
        print(f"  {trad}: {', '.join(pids)}")
    print("\nrefusal rules:")
    for t, r in REFUSALS.items():
        print(f"  {t}: {r[:70]}")
