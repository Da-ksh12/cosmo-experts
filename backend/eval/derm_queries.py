"""Hand-written questions for evaluating retrieval over the dermatology education pack (content/derm).

In scope: a question the pack answers, with the article that answers it (gold). Out of scope: far (nothing to do with health) and near (a skin or health question that the pack does NOT cover,
where answering from the pack would be wrong). The phrasing deliberately avoids copying the articles' own keywords where it can, because the keyword router handles the easy cases and retrieval
is the fallback for everything else. Written by one author; the split into development and held-out is by position, not independent. Evidence class: synthetic_fixture."""
from __future__ import annotations

IN_SCOPE: list[tuple[str, str]] = [
    # acne
    ("How do I get rid of the pimples on my chin?", "derm.acne"), ("Why do I keep getting breakouts around my jawline?", "derm.acne"),
    ("Is it okay to pop a whitehead?", "derm.acne"), ("What kind of face wash is gentle for skin that breaks out?", "derm.acne"),
    ("How long before an acne routine starts working?", "derm.acne"), ("Does being dirty cause blackheads?", "derm.acne"),
    # marks and scars
    ("The dark marks left after my pimples are not fading", "derm.acne-marks-scars"), ("How long do post pimple marks take to go away?", "derm.acne-marks-scars"),
    ("What is the difference between an acne scar and a mark?", "derm.acne-marks-scars"), ("Can I use a laser at home for pitted scars?", "derm.acne-marks-scars"),
    # pigmentation
    ("I have brown patches on both cheeks, what could they be?", "derm.pigmentation"), ("What helps even out my skin tone?", "derm.pigmentation"),
    ("Does sunlight make melasma darker?", "derm.pigmentation"), ("Is a fairness cream safe to lighten my face?", "derm.pigmentation"),
    # redness
    ("My face turns red and stings after I apply a new cream", "derm.redness-irritation"), ("What can I do for irritated, sore skin?", "derm.redness-irritation"),
    ("Why does my skin look red and feel hot?", "derm.redness-irritation"), ("How can I calm red irritated cheeks at home?", "derm.redness-irritation"),
    # rosacea
    ("My cheeks flush when I drink hot tea or spicy food", "derm.rosacea"), ("What triggers facial flushing and tiny visible veins?", "derm.rosacea"),
    ("Can facial flushing be cured?", "derm.rosacea"), ("Is a red nose with small bumps something that needs a doctor?", "derm.rosacea"),
    # dry skin
    ("My skin feels tight and flaky after a shower", "derm.dry-skin-barrier"), ("How hot should my shower be if I have dry skin?", "derm.dry-skin-barrier"),
    ("Which type of moisturiser holds water in best?", "derm.dry-skin-barrier"), ("What is the skin barrier and how do I protect it?", "derm.dry-skin-barrier"),
    # eczema
    ("I have itchy dry patches that come and go", "derm.eczema"), ("Is eczema contagious?", "derm.eczema"),
    ("How do I stop scratching itchy eczema patches?", "derm.eczema"), ("My eczema has started to ooze and crust", "derm.eczema"),
    # product reactions
    ("How do I test a new cream before putting it on my face?", "derm.product-reactions"), ("I think I am allergic to my moisturiser, what should I do?", "derm.product-reactions"),
    ("What is the difference between fragrance free and unscented?", "derm.product-reactions"), ("A new serum is stinging my skin", "derm.product-reactions"),
    # sun protection
    ("How much sunscreen should I put on my face?", "derm.sun-protection"), ("How often should I reapply sunscreen outdoors?", "derm.sun-protection"),
    ("Do I need sunscreen on a cloudy day?", "derm.sun-protection"), ("Is a mineral sunscreen better for sensitive skin?", "derm.sun-protection"),
    # routine
    ("What is a simple skincare routine for the morning?", "derm.routine-basics"), ("In what order should I apply my skincare products?", "derm.routine-basics"),
    ("How many products do I really need?", "derm.routine-basics"), ("How do I know my skin type?", "derm.routine-basics"),
    # ingredients
    ("What does salicylic acid do for skin?", "derm.ingredients"), ("Can I use retinol and vitamin C together?", "derm.ingredients"),
    ("What is benzoyl peroxide used for?", "derm.ingredients"), ("What is the difference between a cream, a lotion and an ointment?", "derm.ingredients"),
    # habits
    ("What common skincare mistakes make skin worse?", "derm.habits-worsen"), ("Should I use lemon juice or toothpaste on pimples?", "derm.habits-worsen"),
    ("Is scrubbing my face hard good for clearing it?", "derm.habits-worsen"), ("Why should I not keep switching products every week?", "derm.habits-worsen"),
    # monitoring
    ("How do I keep track of whether my skin is improving?", "derm.monitoring"), ("What should I write down before seeing a dermatologist?", "derm.monitoring"),
    ("How should I take photos to compare my skin over time?", "derm.monitoring"), ("What should I bring to my skin appointment?", "derm.monitoring"),
    # when to see
    ("When is a skin problem serious enough for a doctor?", "derm.when-to-see"), ("How long should I wait before getting a rash checked?", "derm.when-to-see"),
    ("Which skin symptoms need to be seen on the same day?", "derm.when-to-see"), ("Can I manage this myself or do I need a dermatologist?", "derm.when-to-see"),
    # moles
    ("How do I tell if a mole is dangerous?", "derm.moles-and-spots"), ("What do the ABCDE signs of skin cancer mean?", "derm.moles-and-spots"),
    ("A spot on my arm has grown and bleeds sometimes", "derm.moles-and-spots"), ("Should I be worried about a dark line under my nail?", "derm.moles-and-spots"),
    # warning signs
    ("What does a skin infection look like?", "derm.warning-signs"), ("When is a rash an emergency?", "derm.warning-signs"),
    ("What is the glass test for a rash?", "derm.warning-signs"), ("Which signs mean a skin reaction is serious?", "derm.warning-signs"),
    # photo limits
    ("Can a photo show whether I have a skin condition?", "derm.photo-limits"), ("Why is redness harder to see in photos of darker skin?", "derm.photo-limits"),
    ("How accurate is checking skin from a picture?", "derm.photo-limits"), ("What lighting is best for skin photos?", "derm.photo-limits"),
]

OUT_FAR: list[str] = [
    "Who won the cricket world cup in 1983?", "What is the capital of Australia?", "How do I cook basmati rice?", "Can you recommend a good smartphone?",
    "What is the exchange rate of the dollar today?", "How do I fix a flat bicycle tyre?", "Tell me a joke", "What is the weather like tomorrow?",
    "Who is the prime minister of India?", "How do I learn to play the guitar?", "What time does the train to Delhi leave?", "Write a poem about the sea",
    "How do I invest in mutual funds?", "What is the speed of light?",
]

OUT_NEAR: list[str] = [
    "How much does a hair transplant cost?", "What dose of antibiotic should I take for a throat infection?", "Which tablet is best for hair growth?",
    "How do I remove a tattoo?", "What is the best diet for diabetes?", "Can you read my blood test result?", "How do I treat head lice?",
    "What is the best shampoo for dandruff?", "How should I take care of my teeth?", "Is botox safe during pregnancy?", "How do I treat a fungal infection in my toenail?",
    "What is the normal blood pressure range?", "Which vitamin injection should I take for energy?", "How can I lose weight quickly?", "How do I get rid of warts at home?",
    "What are the side effects of paracetamol?", "How do I treat a cold?", "Is it safe to take isotretinoin for acne?", "How do I prescribe myself a steroid cream?",
    "What is the treatment for vitiligo?", "How many calories are in a banana?", "How is psoriasis treated with biologics?", "How can I make my hair grow faster?",
    "What does my thyroid report mean?", "Can I stop my blood pressure tablets?", "How do I choose a good gym?",
]


def split(items: list, every: int = 2) -> tuple[list, list]:
    """Development = positions where index % every != 0 ... held-out = the rest (a fixed, reproducible split)."""
    dev = [x for i, x in enumerate(items) if i % every != 0]
    held = [x for i, x in enumerate(items) if i % every == 0]
    return dev, held


DEV_IN, HELDOUT_IN = split(IN_SCOPE, 2)
DEV_OUT_FAR, HELDOUT_OUT_FAR = split(OUT_FAR, 2)
DEV_OUT_NEAR, HELDOUT_OUT_NEAR = split(OUT_NEAR, 2)
