"""
Tests for the Whisper hallucination filter.

The positive cases are real transcript entries captured on 2026-08-14, when
CPU starvation truncated captures and Whisper looped on the resulting ~1s
clips. Every one of them passed the old sentence-level filter untouched.

The negative cases matter just as much: scanner traffic legitimately repeats
itself (unit roll-calls, read-backs, doubled acknowledgements), and a filter
that eats those is worse than no filter at all.
"""
import pytest

from app.audio.transcriber import (
    _PHRASE_REPEAT_DROP_COUNT,
    _filter_hallucination,
    _max_phrase_repetition,
)


# Verbatim from ~/Documents/NeoSCAN-Transcripts/2026-08-14.txt, 14:00-17:59.
LOOPS = [
    "Desiphoning Desiphoning Desiphoning Desiphoning Desiphoning Desiphoning",
    "BE BE BE BE BE BE BE BE BE BE BE BE BE BE",
    "Now, listen, listen, listen, listen, listen, listen, listen, listen,",
    "Yes, Desiree, Desiree, Desiree, Desiree, Desiree, Desiree Desiree",
    "Now grab the quest quest quest quest quest quest quest quest quest",
    "Now using using using using using using using using",
    "I'm ready to go I'm ready to go I'm ready to go I'm ready to go",
    "Descent 3, Descent 3, Descent 3, Descent 3, Descent 3, Descent 3,",
    "Indeed, quest fee fee fee fee fee fee fee fee fee fee fee fee fee",
    "Now, you can grab the phone, grab the phone, grab the phone, "
    "grab the phone, grab the phone,",
]

# Real loops from the same window that this filter deliberately does NOT catch.
#
# Both are a longer block emitted exactly twice. Structurally that is
# indistinguishable from a real transmission sent twice — the "roll-call
# repeated twice" case in KEEPERS below has the identical shape. Catching these
# would mean dropping to a two-repeat threshold, which would start eating real
# traffic, so we accept leaving them in rather than risk losing transmissions.
# Separating them needs a second signal (e.g. no_speech_prob), not more
# aggressive repetition matching.
KNOWN_UNCAUGHT = [
    "W1 Descent, Descent Descent W1 Descent, Descent Descent",
    "Desiphoning forces using forces using forces Desiphoning forces "
    "using forces using forces",
]

# Real transmissions that must survive untouched.
KEEPERS = [
    "Station S-94, respond to 308 Hartford Turnpike, Brody's Restaurant, "
    "for a 25-year-old female having an anxiety attack.",
    "Medic 2, fire alarm. Answering Medic 2. We're back in town. "
    "Medic 2, back in town, 12 o'clock.",
    # Roll-call: many similar-looking but distinct tokens.
    "Engine 1, Engine 3, Engine 2, Car 1, Squad 1, Car 5, Medic 1, ES, Medic 1R",
    # The same roll-call transmitted twice — repetition, but only twice.
    "Engine 1, Engine 3, Engine 2, Car 1, Squad 1, "
    "Engine 1, Engine 3, Engine 2, Car 1, Squad 1",
    "Received. Received.",
    "10-4, 10-4",
    "Engine 1 to follow up. Answering, Engine 1. Engine 1 is available.",
    "94, escort 4. Received, 630.",
]


@pytest.mark.parametrize("text", LOOPS)
def test_decode_loops_are_dropped(text):
    assert _filter_hallucination(text, []) == ""


@pytest.mark.parametrize("text", KEEPERS)
def test_real_traffic_is_kept(text):
    assert _filter_hallucination(text, []) == text.strip()


@pytest.mark.xfail(
    reason="two-repeat loops are indistinguishable from a doubled real "
           "transmission; needs a second signal to separate",
    strict=False,
)
@pytest.mark.parametrize("text", KNOWN_UNCAUGHT)
def test_two_repeat_loops_are_a_known_gap(text):
    assert _filter_hallucination(text, []) == ""


def test_sentence_level_loop_still_dropped():
    """The original "Thank you." case must keep working."""
    assert _filter_hallucination("Thank you. Thank you. Thank you.", []) == ""


def test_pure_punctuation_dropped():
    assert _filter_hallucination("! ... ...", []) == ""


def test_all_segments_no_speech_dropped():
    segments = [{"no_speech_prob": 0.95, "text": "Roger"}]
    assert _filter_hallucination("Roger", segments) == ""


def test_single_real_segment_kept():
    segments = [{"no_speech_prob": 0.2, "text": "Roger"}]
    assert _filter_hallucination("Roger", segments) == "Roger"


class TestMaxPhraseRepetition:
    def test_single_word_run(self):
        reps, covered = _max_phrase_repetition(["be"] * 7)
        assert reps == 7
        assert covered == 7

    def test_multi_word_phrase(self):
        tokens = "i'm ready to go".split() * 4
        reps, covered = _max_phrase_repetition(tokens)
        assert reps == 4
        assert covered == 16

    def test_no_repetition(self):
        reps, covered = _max_phrase_repetition("engine one car five medic two".split())
        assert reps == 1
        assert covered == 0

    def test_non_consecutive_repeats_do_not_count(self):
        # "engine" recurs but never back-to-back — a real roll-call pattern.
        tokens = "engine one engine three engine two car one".split()
        reps, _ = _max_phrase_repetition(tokens)
        assert reps < _PHRASE_REPEAT_DROP_COUNT
