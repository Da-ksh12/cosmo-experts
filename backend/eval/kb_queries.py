"""Hand-written evaluation queries for passage retrieval over the knowledge base.

EVALUATION CORPUS: the 11 DRAFT articles shipped in content/kb_drafts.json. They are unreviewed and are used here only as text to retrieve from; nothing in this evaluation says the articles
are clinically correct, and it says nothing about the content of any article a clinic approves.

QUERIES: written by one author (not patients, not independent of the system design). Two sets, written together before any retrieval was run and never edited afterwards:
* DEV selects the abstention threshold and the configuration;
* HELDOUT is run once with the choice already made, and reported next to the development figures.
Each in-scope query names the article that answers it and a few words of the answering sentence (`support`); a retrieved passage counts as supporting only if it contains one of them.
Out-of-scope queries are of two kinds: `far` (nothing to do with health or the app) and `near` (health or lab wording the corpus does not answer, which is where a retriever is most
likely to answer wrongly)."""

# (query, article_id, [support phrases: at least one must appear in the top passage])
DEV_IN = [
    ("what does reference range mean on my lab report", "kb-reference-range", ["set of values"]),
    ("who decides the normal range printed on a report", "kb-reference-range", ["laboratory printed"]),
    ("why is a range printed next to each result", "kb-reference-range", ["reference range"]),
    ("does one abnormal test mean I have a disease", "kb-single-result", ["single test value", "interprets results"]),
    ("can a doctor diagnose from one number", "kb-single-result", ["interprets results", "single test value"]),
    ("is one blood result enough to know what is wrong", "kb-single-result", ["cannot tell the full story"]),
    ("what does the H next to my result mean", "kb-high-low-flags", ["high or low marker"]),
    ("my result has an L mark, is that bad", "kb-high-low-flags", ["does not mean something is wrong", "high or low marker"]),
    ("what is the meaning of the high and low flags", "kb-high-low-flags", ["outside the reference range", "high or low marker"]),
    ("why do two labs give different normal ranges", "kb-lab-differences", ["equipment, methods"]),
    ("can I compare reports from different laboratories", "kb-lab-differences", ["compare each result"]),
    ("my old report has another range than the new one", "kb-lab-differences", ["may differ from another", "equipment, methods"]),
    ("why does the unit matter", "kb-units", ["only makes sense with its unit"]),
    ("can I compare mg/dL with g/dL numbers", "kb-units", ["not comparable", "different units"]),
    ("same test different units in two reports", "kb-units", ["different units by different laboratories", "different units"]),
    ("do I have to fast before a blood test", "kb-test-preparation", ["eating, drinking"]),
    ("how should I get ready for a lab test", "kb-test-preparation", ["preparation depends"]),
    ("should I stop my medicine before a test", "kb-test-preparation", ["do not change any medicine"]),
    ("what should I ask my doctor at the appointment", "kb-questions-for-doctor", ["useful questions"]),
    ("should I bring my reports to the visit", "kb-questions-for-doctor", ["bring your reports"]),
    ("how can the clinic see my report", "kb-questions-for-doctor", ["add a report in this app"]),
    ("can this assistant diagnose me", "kb-assistant-limits", ["does not diagnose"]),
    ("what can this chatbot do", "kb-assistant-limits", ["show you what your uploaded reports say"]),
    ("what should I do if I feel very unwell", "kb-assistant-limits", ["emergency number", "feel very unwell"]),
    ("what does reviewed by clinician mean", "kb-clinician-review", ["signed-in doctor"]),
    ("has a person checked my result or only the software", "kb-clinician-review", ["processed by software"]),
    ("can the software make mistakes with blurry documents", "kb-clinician-review", ["software results can contain mistakes"]),
    ("how do I take a good photo for the clinic", "kb-photo-quality", ["even, bright light"]),
    ("the app says my photo is not clear enough, what now", "kb-photo-quality", ["retake it"]),
    ("should I put cream on before the photo", "kb-photo-quality", ["before applying products"]),
    ("is the quotation the final price", "kb-quotation", ["final cost depends"]),
    ("when can I see my quotation", "kb-quotation", ["only after a clinic doctor approves"]),
    ("what is a quote based on", "kb-quotation", ["approved price list"]),
]

DEV_OUT_FAR = [
    "who won the cricket world cup in 1983", "how do I cook pasta", "what is the weather in Delhi today", "write a poem about the sea", "what is the capital of France",
    "how do I change my phone password", "recommend a good movie", "explain quantum computing", "how many steps did I walk yesterday", "translate good morning into French",
    "what is the best laptop to buy", "how do I book a flight to Mumbai", "tell me a joke", "who is the prime minister", "how to fix a flat bicycle tyre",
]
DEV_OUT_NEAR = [
    "what is a normal ferritin level for women", "how is type 2 diabetes treated", "what does a high TSH value mean for me", "which cream is best for acne scars",
    "how many days should I take antibiotics", "is paracetamol safe in pregnancy", "what foods lower cholesterol", "how long does a hair transplant take to heal",
    "what is the normal range of vitamin D", "can I take ibuprofen with my blood pressure tablet", "what is the treatment for dandruff", "how do I read an ECG",
    "what causes hair fall in women", "what side effects does metformin have", "how often should I get a lipid profile",
]

HELDOUT_IN = [
    ("what is meant by a reference interval", "kb-reference-range", ["set of values", "laboratory printed"]),
    ("where does the typical range on my result sheet come from", "kb-reference-range", ["laboratory printed", "set of values"]),
    ("different methods can print different ranges, is that so", "kb-reference-range", ["different ranges", "methods"]),
    ("if one test value is out of range am I ill", "kb-single-result", ["single test value", "interprets results"]),
    ("how do doctors interpret my lab values", "kb-single-result", ["interprets results"]),
    ("a lone result does not tell everything, right", "kb-single-result", ["cannot tell the full story"]),
    ("what do the red H and L letters on my report stand for", "kb-high-low-flags", ["high or low marker"]),
    ("does a marked value mean something is wrong", "kb-high-low-flags", ["does not mean something is wrong"]),
    ("a result is marked low, who decides what that means", "kb-high-low-flags", ["your doctor decides"]),
    ("why is my range different from my friend's report", "kb-lab-differences", ["may differ from another", "equipment, methods"]),
    ("should I judge each result against the range on its own report", "kb-lab-differences", ["compare each result"]),
    ("do all laboratories use the same equipment and reference groups", "kb-lab-differences", ["equipment, methods"]),
    ("what happens if I ignore the unit printed beside a number", "kb-units", ["read the unit", "only makes sense"]),
    ("results in mg/dL versus g/dL, are they the same thing", "kb-units", ["not comparable", "different units"]),
    ("why shouldn't I compare numbers that have different units", "kb-units", ["do not compare numbers", "not comparable"]),
    ("are there instructions about eating or drinking before my test", "kb-test-preparation", ["eating, drinking"]),
    ("who tells me how to prepare for the test", "kb-test-preparation", ["follow the written instructions", "preparation depends"]),
    ("can I change my tablets on my own before the lab visit", "kb-test-preparation", ["do not change any medicine"]),
    ("how do I get ready for a consultation", "kb-questions-for-doctor", ["write down what you want to ask", "useful questions"]),
    ("what questions are useful to ask about a result", "kb-questions-for-doctor", ["useful questions"]),
    ("how do I add a report so the clinic team can see it", "kb-questions-for-doctor", ["add a report in this app"]),
    ("will this assistant choose my medicine dose", "kb-assistant-limits", ["choose medicines or doses"]),
    ("what is this assistant allowed to help with", "kb-assistant-limits", ["show you what your uploaded reports say"]),
    ("can the assistant tell me if it is an emergency", "kb-assistant-limits", ["emergency"]),
    ("how do I know a doctor looked at my item", "kb-clinician-review", ["signed-in doctor", "recorded their review"]),
    ("items without the reviewed label, who checked them", "kb-clinician-review", ["processed by software"]),
    ("are automatic readings of unusual documents reliable", "kb-clinician-review", ["software results can contain mistakes"]),
    ("tips for a clear photograph in good light", "kb-photo-quality", ["even, bright light"]),
    ("how steady should I hold the camera and where to focus", "kb-photo-quality", ["hold the camera steady"]),
    ("what do I do when the app rejects a blurry photo", "kb-photo-quality", ["retake it"]),
    ("does the quote I get show the final cost", "kb-quotation", ["final cost depends"]),
    ("who has to approve a quotation before I can see it", "kb-quotation", ["only after a clinic doctor approves"]),
    ("what is a quotation from the clinic", "kb-quotation", ["estimate prepared"]),
]

HELDOUT_OUT_FAR = [
    "what is the tallest mountain in the world", "how do I bake bread at home", "will it rain in Mumbai tomorrow", "compose a song about friendship", "who painted the Mona Lisa",
    "how can I reset my email account", "suggest a book to read on holiday", "what is machine learning", "how many calories did I burn running yesterday", "translate thank you into Spanish",
    "which phone has the best camera", "how do I renew my passport", "give me a riddle", "who is the president of the United States", "how to tie a necktie",
]
HELDOUT_OUT_NEAR = [
    "what is the normal haemoglobin range in men", "how do you treat high blood pressure", "what does a low vitamin B12 mean for me", "which shampoo is best for hair loss",
    "how long should I take an antibiotic course", "is aspirin safe for children", "which foods raise uric acid", "how long does it take for a scar to fade",
    "what is the usual creatinine range", "can I drink alcohol while taking metronidazole", "how do I treat an ingrown hair", "how do I read a chest x-ray",
    "what causes dark circles under the eyes", "what are the side effects of isotretinoin", "how often should I check my blood sugar",
]
