# Clinical review packet (DRAFT, nothing here is approved)

Prepared for: a qualified clinician (the clinic's medical lead or a delegate) who will correct, approve or reject the content below.

**Status:** every item is a software-written draft. The patient assistant does **not** use any of it. The assistant currently answers only from the patient's own documents, from clinician-approved articles (none are approved yet) and from approved quotations, and uses a small built-in emergency-redirect default.

## What we need from you

1. For each knowledge-base article (Part A): approve, edit or reject, and note the source you checked it against.
2. For the urgent-symptom policy (Part B): confirm or change the categories, phrases and the message, and answer the open questions.
3. Tell us which languages must be supported and who verifies Hindi/Hinglish wording (Part C).

How approval is recorded and enforced: see `APPROVAL_PROCEDURE.md`. Sign-off page: `SIGNOFF_TEMPLATE.md`. Spreadsheet versions: `kb_review.csv`, `urgent_policy_review.csv`.

Files reviewed (hashes identify the exact draft version):

* `backend/content/kb_drafts.json`: 11 articles
* `backend/content/urgent_policy.draft.json`: content hash `485e053ca5f50eb9…`

## Part A: knowledge-base articles

### A1. What is a reference range?

`kb-reference-range`, language `en`, text hash `2c7b215b088a9613…`

> A reference range is the set of values the laboratory printed on your report as typical for the test it used. Different laboratories and methods can print different ranges.

Statements to verify:

- [ ] A reference range is printed by the laboratory for the method it used.
- [ ] Different laboratories or methods can print different ranges.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A2. Why one result is not a diagnosis

`kb-single-result`, language `en`, text hash `14ebfb425d31ae4e…`

> A single test value cannot tell the full story. A doctor interprets results together with your symptoms, history, examination and other tests.

Statements to verify:

- [ ] A single value alone does not establish a diagnosis.
- [ ] Interpretation combines symptoms, history, examination and other tests.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A3. What do the High and Low markers on a report mean?

`kb-high-low-flags`, language `en`, text hash `5123bf6895f8e62c…`

> A High or Low marker means the printed value is outside the reference range shown next to it on your report. It tells you the value sits outside that printed range. It does not tell you why, and it does not mean something is wrong or right. Your doctor decides what, if anything, it means for you.

Statements to verify:

- [ ] A High/Low marker only states position relative to the printed reference range.
- [ ] Being outside the printed range does not by itself indicate a problem; interpretation is the doctor's.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A4. Why can two laboratories print different ranges for the same test?

`kb-lab-differences`, language `en`, text hash `32349cbe781a005f…`

> Laboratories may use different equipment, methods and reference groups, so the range printed on one report may differ from another. When you compare reports from different laboratories, compare each result with the range printed on its own report, and ask your doctor if the difference matters.

Statements to verify:

- [ ] Equipment, methods and reference populations can differ between laboratories and change the printed range.
- [ ] Each result should be compared with the range on the same report.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A5. Why do units matter on a report?

`kb-units`, language `en`, text hash `d581ff5a63c44d87…`

> A test value only makes sense with its unit, for example mg/dL or g/dL. The same test can be reported in different units by different laboratories, and a number in one unit is not comparable to a number in another. Always read the unit printed beside the value and do not compare numbers that have different units.

Statements to verify:

- [ ] The same analyte can be reported in different units by different laboratories.
- [ ] Values in different units are not directly comparable without conversion.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A6. Do I need to prepare for a test?

`kb-test-preparation`, language `en`, text hash `9b3ca678a159c326…`

> Preparation depends on the test and on the laboratory. Some tests have instructions about eating, drinking or medicines beforehand and others do not. Follow the written instructions from your doctor or laboratory, and ask them if you are unsure. Do not change any medicine unless your doctor tells you to.

Statements to verify:

- [ ] Preparation requirements vary by test and laboratory.
- [ ] Patients should follow their doctor's or laboratory's instructions and not change medicines on their own.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A7. How to prepare questions for your doctor

`kb-questions-for-doctor`, language `en`, text hash `a235b4949d792f7c…`

> Before an appointment you can write down what you want to ask. Useful questions include: what does this result mean for me, is any follow-up needed, when should the test be repeated, and who should I contact if something changes. Bring your reports with you. You can add a report in this app so that the clinic team can see it.

Statements to verify:

- [ ] Preparing questions and bringing reports is reasonable general advice.
- [ ] The example questions are appropriate and not misleading.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A8. What this assistant can and cannot do

`kb-assistant-limits`, language `en`, text hash `2bc3dccf75d13d90…`

> This assistant can show you what your uploaded reports say, share information your clinic has approved, and help you prepare questions. It does not diagnose, choose medicines or doses, or tell you whether a situation is an emergency. If you feel very unwell or think you may need urgent help, contact your local emergency number or your clinic straight away.

Statements to verify:

- [ ] The description of the assistant's capabilities matches the product behaviour.
- [ ] The emergency instruction wording is acceptable to the clinic (see the urgent-symptom policy draft).

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A9. What does 'reviewed by a clinician' mean in this app?

`kb-clinician-review`, language `en`, text hash `212cfe5bb54a0b8d…`

> When an item says it was reviewed by a clinician, a signed-in doctor at your clinic has looked at it and recorded their review. Items without this label have only been processed by software and have not been checked by a person. Software results can contain mistakes, especially for blurry or unusual documents and photos.

Statements to verify:

- [ ] The app labels an item reviewed only after a doctor's recorded review.
- [ ] The statement about software mistakes is accurate and appropriately worded.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A10. How do I take a clear photo for the clinic?

`kb-photo-quality`, language `en`, text hash `419fff545cf80b6d…`

> Use even, bright light such as daylight and avoid strong shadows and flash glare. Hold the camera steady, fill most of the frame with the area to be looked at, and tap to focus before taking the photo. Take the photo before applying products to the area unless your clinic tells you otherwise. If the app says the photo is not clear enough, retake it.

Statements to verify:

- [ ] Lighting, focus and framing guidance is appropriate.
- [ ] The advice about products before photographing matches clinic practice or should be removed.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A11. What is a quotation and is it the final price?

`kb-quotation`, language `en`, text hash `f2bf60baac4609d2…`

> A quotation is an estimate prepared from the clinic's approved price list. It becomes available to you only after a clinic doctor approves it. The final cost depends on the plan your doctor agrees with you, so ask the clinic if anything is unclear.

Statements to verify:

- [ ] The quotation is an estimate and not the final bill under clinic policy.
- [ ] The approval step description matches how the clinic works.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

## Part B: urgent-symptom policy

**Proposed patient message** (needs your approval and the real emergency number/clinic contact):

> This may be an emergency. Please call your local emergency number or go to the nearest emergency department now. If you are with someone who is unwell, ask them to help you. You can also call the clinic. This assistant cannot assess emergencies and cannot tell you that a situation is safe.

**Design decisions that need your answer:**

- [ ] Over-escalate or under-escalate? The draft errs towards escalating (e.g. 'no chest pain' also triggers) because negation handling by pattern is unreliable. Confirm or change.
- [ ] Should 'high fever' escalate for adults, or only for infants/very young children? Needs an age-aware rule or a fixed message.
- [ ] Is the proposed message wording acceptable? Should it name a specific local emergency number for the clinic's country/region?
- [ ] Should the clinic phone number and opening hours be shown in the message?
- [ ] Which languages must be supported at launch (English only, Hindi in Devanagari, Hinglish in Latin script)?

**Limits of the mechanism (please read):**

- Keyword/regular-expression matching on typed or transcribed text. It cannot understand context, negation, spelling errors, or languages not listed.
- It is a safety net that redirects to emergency services. It is NOT a triage tool and never says a situation is safe.
- A statement that does not match is NOT evidence that there is no emergency.

### B: Difficulty breathing (`breathing`)

Why: Breathing difficulty can be life-threatening.

Phrases that trigger it (regular expressions):

- `\b(can'?t|cannot|unable to) (breathe|catch (my|a) breath)\b`
- `\b(difficulty|trouble|struggling) (in )?breathing\b`
- `\bshort(ness)? of breath\b`
- `\bgasping\b`
- `\bchoking\b`

Should trigger: “I can't breathe”; “I have difficulty breathing”; “shortness of breath since morning”; “my son is choking”

Should not trigger: “How do I read my breathing test report?”; “What is the normal range for oxygen saturation?”

Questions:

- [ ] Is 'shortness of breath' too broad (chronic stable conditions)? Should it still escalate?

Decision: [ ] keep  [ ] change  [ ] remove

### B: Chest pain or pressure (`chest`)

Why: Chest pain can indicate a cardiac emergency.

Phrases that trigger it (regular expressions):

- `\bchest (pain|tightness|pressure|discomfort)\b`
- `\b(pain|pressure|tightness|discomfort) (in|on) (my |the |his |her )?chest\b`
- `\bheart attack\b`

Should trigger: “I have chest pain”; “pressure in my chest”; “I think it is a heart attack”

Should not trigger: “What does the chest X-ray report mean?”; “Where can I get a heart scan done?”

Questions:

- [ ] Should 'palpitations' or 'pain spreading to the arm or jaw' be added?

Decision: [ ] keep  [ ] change  [ ] remove

### B: Severe or uncontrolled bleeding (`bleeding`)

Why: Heavy bleeding may need immediate care.

Phrases that trigger it (regular expressions):

- `\bsevere bleeding\b`
- `\b(heavy|uncontrolled|won'?t stop) bleeding\b`
- `\bbleeding (heavily|a lot|won'?t stop|that (will not|won'?t) stop)\b`
- `\bcoughing (up )?blood\b`
- `\bvomiting blood\b`

Should trigger: “there is severe bleeding”; “the bleeding won't stop”; “I am vomiting blood”

Should not trigger: “Is a small cut bleeding normal after a peel?”; “What is a bleeding time test?”

Questions:

- [ ] Where is the line between minor and severe bleeding for post-procedure care? Should the clinic's own after-care guidance be referenced?

Decision: [ ] keep  [ ] change  [ ] remove

### B: Loss of consciousness, fainting, confusion (`consciousness`)

Why: Altered consciousness may signal a serious condition.

Phrases that trigger it (regular expressions):

- `\bunconscious\b`
- `\bpassed out\b`
- `\bfaint(ed|ing)\b`
- `\bnot (responding|waking up)\b`
- `\bsudden(ly)? confus(ed|ion)\b`

Should trigger: “he is unconscious”; “I fainted”; “she is not responding”

Should not trigger: “What does 'faint line' mean on my pregnancy test report?”

Questions:

- [ ] 'faint' is also used for non-medical meanings (faint line, faint colour). Accept the false positives?

Decision: [ ] keep  [ ] change  [ ] remove

### B: Severe allergic reaction (`allergic_reaction`)

Why: Rapid facial or throat swelling can obstruct the airway.

Phrases that trigger it (regular expressions):

- `\banaphyla\w*\b`
- `\bswelling of (the )?(face|lips|tongue|throat)\b`
- `\b(face|lips|tongue|throat) (is |are )?swell(ing|ed|en)\b`
- `\bthroat (is )?closing\b`

Should trigger: “my lips are swelling after the injection”; “swelling of the tongue”; “I think it is anaphylaxis”

Should not trigger: “Is mild swelling around the eyes normal the day after a filler?”

Questions:

- [ ] For a dermatology clinic: should post-procedure swelling of the face have its own, less urgent wording?

Decision: [ ] keep  [ ] change  [ ] remove

### B: Possible stroke signs (`stroke_signs`)

Why: Sudden neurological symptoms are time-critical.

Phrases that trigger it (regular expressions):

- `\bface (is )?droop(ing|ed)?\b`
- `\bslurred speech\b`
- `\bsudden(ly)? (weak|numb)\w*\b`
- `\bcan'?t (move|lift) (my )?(arm|leg)\b`
- `\bsudden (loss of vision|vision loss)\b`

Should trigger: “my face is drooping”; “slurred speech”; “sudden weakness on one side”

Should not trigger: “What is a nerve conduction study?”

Questions:

- [ ] Add 'sudden severe headache'? Add 'worst headache of my life'?

Decision: [ ] keep  [ ] change  [ ] remove

### B: Seizure (`seizure`)

Why: Seizures may need emergency care.

Phrases that trigger it (regular expressions):

- `\bseizure\b`
- `\bconvuls(ion|ing)\b`
- `\bfitting\b`

Should trigger: “he is having a seizure”; “she is convulsing”

Should not trigger: “Who can interpret my EEG report?”

Questions:

- [ ] 'fitting' also means clothing or equipment fitting. Accept the false positives?

Decision: [ ] keep  [ ] change  [ ] remove

### B: Thoughts of self-harm or suicide (`self_harm`)

Why: Safety of the person is the priority.

Phrases that trigger it (regular expressions):

- `\bsuicid\w*\b`
- `\b(kill|hurt|harm) myself\b`
- `\bend my life\b`
- `\bdon'?t want to (live|be alive)\b`
- `\bwant to die\b`

Should trigger: “I want to end my life”; “I am thinking of suicide”; “I want to hurt myself”

Known false alarms (accepted by the draft): “What does 'suicide inhibitor' mean in an enzyme report?”

Questions:

- [ ] This category needs its own message (compassionate wording and a local crisis line), not the general emergency message. Please supply approved wording and the number.
- [ ] Should the clinic be notified automatically? (Not implemented.)

Decision: [ ] keep  [ ] change  [ ] remove

### B: Poisoning or overdose (`poisoning`)

Why: Poisoning and overdose are emergencies.

Phrases that trigger it (regular expressions):

- `\boverdos(e|ed|ing)\b`
- `\bpoison(ed|ing)?\b`
- `\bswallowed (a lot|too many|bleach|chemical)\w*\b`

Should trigger: “I took an overdose”; “my child swallowed bleach”

Known false alarms (accepted by the draft): “Is poison ivy rash dangerous?”

Questions:

- [ ] Include 'took too many tablets' phrasing variants?

Decision: [ ] keep  [ ] change  [ ] remove

### B: High fever (`fever`)

Why: Persistent or very high fever, especially in infants or immunosuppressed people, may need prompt care.

Phrases that trigger it (regular expressions):

- `\bhigh fever\b`
- `\bfever (of|above|over) 10[2-9]\b`

Should trigger: “I have a high fever”; “fever of 104”

Should not trigger: “What is the normal body temperature?”

Questions:

- [ ] Needs age and context rules. As drafted it is a placeholder matching the current built-in default. Please specify thresholds or remove.

Decision: [ ] keep  [ ] change  [ ] remove

## Part C: Hindi / Hinglish

Unverified. These transliterations were proposed by a model and have not been checked by a Hindi-speaking clinician. They are NOT part of the category patterns above.

- breathing: “saans nahi aa rahi”; “saans lene mein dikkat”
- chest: “seene mein dard”; “chhati mein dard”
- bleeding: “bahut khoon beh raha hai”
- consciousness: “behosh ho gaya”; “behosh ho gayi”
- self_harm: “jaan dena chahta hoon”; “mar jana chahta hoon”

Devanagari: Not drafted: a clinician must supply and verify.

## What the software tests already prove (and what it cannot)

Proven by `backend/tests/test_clinical_content.py`: drafts are never used; approval needs a doctor, an attestation and a source note; approval is bound to the exact text (an edit or a direct database change withdraws it); a tampered or malformed approved-policy file is ignored in favour of the built-in default; the draft patterns match their own examples and cover everything the built-in default covers.

**Not proven and not provable by software:** clinical correctness of any sentence, the right set of emergency phrases, or that keyword matching catches real patient wording. Matching is on typed or transcribed text, cannot understand context or negation, and a non-match must never be read as 'safe'.
