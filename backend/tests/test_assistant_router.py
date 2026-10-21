"""Which part of the system answers a message: the router (assistant/router.py) sends a photo question to the photo, a skin question to the library, a report question to the report, and so on,
in a fixed order of precedence, in English, Hinglish and Hindi. The rules are word patterns, so this table IS their specification: a change that moves a row must say why."""
import pytest

from app.assistant import router
from app.derm import lexicon

CASES = [
    # (message, has_photo, expected kind, expected sub/request note)
    ("I have pimples on my cheeks", False, "education"), ("How do I get rid of acne?", False, "education"), ("Suggest a gentle skincare routine", False, "education"),
    ("How do I choose a sunscreen?", False, "education"), ("What is the difference between a cream and an ointment?", False, "education"), ("mere chehre par muhase hain", False, "education"),
    ("मुंहासे कैसे ठीक करें", False, "education"), ("why is my skin so dry", False, "education"), ("Should I use sunscreen every day?", False, "education"),
    ("Is eczema contagious?", False, "education"), ("What is acne and why does it happen?", True, "education"),
    ("What is this?", True, "photo"), ("what do you see in my photo", True, "photo"), ("Explain the highlighted area.", True, "photo"), ("Can you find the acne on my face?", True, "photo"),
    ("Can you detect acne from my photo?", False, "photo"), ("is this acne?", True, "photo"), ("how can I take a better photo", True, "photo"), ("Prepare a summary for my dermatologist", True, "photo"),
    ("Prepare a summary for my dermatologist", False, "history"),
    ("Explain my latest report", False, "report"), ("What does my ferritin result say?", False, "report"), ("Upload a medical report", False, "report"), ("what is a reference range", False, "report"),
    ("which medicine is listed on my prescription", False, "report"),
    ("Book an appointment with a dermatologist", False, "booking"), ("how much does a chemical peel cost", False, "booking"), ("what are the clinic timings", False, "booking"),
    ("I would like a quotation", False, "booking"), ("show the price list", False, "booking"), ("appointment chahiye", False, "booking"),
    ("What is my care plan?", False, "care_plan"), ("what did the doctor plan for me", False, "care_plan"),
    ("how am I progressing since my last visit", False, "history"), ("what have I uploaded", False, "history"),
    ("Hello", False, "general"), ("thanks", False, "general"), ("what can you do", False, "general"),
    ("Who won the cricket world cup in nineteen eighty three", False, "unrelated"), ("What is the capital of France?", False, "unrelated"), ("write me a poem", False, "unrelated"),
    ("I have chest pain", False, "unrelated"),                                    # not a skin word: the clinic's urgent policy (checked before routing) is what handles it, see test_assistant_dispatch
    ("my lips and tongue are swollen", False, "urgent"), ("a rash that does not fade under a glass and I have a fever", False, "urgent"), ("red area spreading fast, hot and painful", True, "urgent"),
]


@pytest.mark.parametrize("text,has_photo,kind", CASES)
def test_a_message_goes_to_the_part_of_the_system_that_can_answer_it(text, has_photo, kind):
    r = router.classify(text, has_photo=has_photo)
    assert r.kind == kind, (text, r.kind, r.reasons)
    assert r.category == router.CATEGORY[kind]


def test_safety_comes_before_every_other_category_and_a_warning_sign_in_a_photo_conversation_still_wins():
    r = router.classify("the area in my photo is swollen, hot and spreading fast and I have a fever", has_photo=True)
    assert r.kind == "urgent" and r.triage.level in ("urgent", "emergency")
    r = router.classify("can I book an appointment? my face is swollen and I can't breathe", has_photo=False)
    assert r.kind == "urgent" and r.triage.level == "emergency"


def test_a_prompt_level_sign_is_attached_to_the_answer_not_a_replacement_for_it():
    r = router.classify("a mole on my back is getting bigger and itchy", has_photo=False)
    assert r.kind == "education" and r.topic == "moles" and r.triage.level == "prompt" and r.triage.escalated()
    r = router.classify("my acne is deep and painful and leaving scars", has_photo=False)
    assert r.kind == "education" and r.topic == "acne" and r.triage.level == "prompt"


def test_asking_for_a_diagnosis_or_a_medicine_is_recognised_without_blocking_ordinary_skincare_questions():
    assert router.classify("Do I have acne? Diagnose me", has_photo=False).request == "diagnosis"
    assert router.classify("is this melanoma?", has_photo=True).request == "diagnosis"
    assert router.classify("Which medicine should I take for pimples?", has_photo=False).request == "medication"
    assert router.classify("what dose of doxycycline should I take", has_photo=False).request == "medication"
    assert router.classify("Should I start isotretinoin?", has_photo=False).request == "medication"
    for ok in ("Should I use sunscreen?", "Can I use a vitamin C serum in the morning?", "What does salicylic acid do?", "Which cleanser is gentle for acne?", "which medicine is listed on my prescription"):
        assert router.classify(ok, has_photo=False).request is None, ok


def test_a_short_follow_up_continues_the_previous_topic_but_never_an_emergency_or_a_greeting():
    r = router.classify("and how long will it take?", has_photo=False, prev_kind="education", prev_topic="acne")
    assert r.kind == "education" and r.topic == "acne" and r.inherited
    r = router.classify("how long does it take", has_photo=False, prev_kind="photo", prev_topic=None)
    assert r.kind == "photo" and r.inherited
    assert router.classify("what is the weather", has_photo=False, prev_kind="education", prev_topic="acne").kind == "unrelated"        # a new subject is not forced into the old one
    assert router.classify("and how long will it take?", has_photo=False, prev_kind="urgent").kind == "unrelated"
    assert router.classify("thanks", has_photo=False, prev_kind="education").kind == "general"


def test_a_tapped_question_or_area_makes_it_a_photo_question_even_without_photo_words():
    r = router.classify("Explain: Lighting.", has_photo=True, explicit_photo=True)
    assert r.kind == "photo"
    r = router.classify("What does ferritin mean?", has_photo=True)                                  # a photo in the conversation does not capture every question
    assert r.kind == "report"
    r = router.classify("What is a good sunscreen?", has_photo=True)
    assert r.kind == "education"


def test_booking_words_in_a_question_about_whether_to_see_someone_are_education_not_booking():
    assert router.classify("should I see a dermatologist for this?", has_photo=False).kind != "booking"
    assert router.classify("when should I see a doctor about acne", has_photo=False).kind == "education"
    assert router.classify("how much sunscreen should I use", has_photo=False).kind == "education"       # 'how much' about a product is not a price question
    assert router.classify("how much is the consultation fee", has_photo=False).kind == "booking"


def test_topics_in_the_route_follow_the_question_and_the_concern_is_preferred():
    r = router.classify("what should I avoid with acne and pimples", has_photo=False)
    assert r.topic == "acne" and "habits" in r.topics
    r = router.classify("how do I keep track of dark spots", has_photo=False)
    assert r.topic in lexicon.CONCERN_TOPICS
