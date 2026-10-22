# Dermatology library review packet (DRAFT, nothing here is approved)

Prepared for: the clinic's dermatologist (or a delegate), who will correct, approve or reject the content below.

**Status:** every article and every warning-sign rule is software-written and **pending clinician review** (pack `derm-pack-1.0.0`, fingerprint `ebd776bc2b44b740…`). Until an article is approved in the clinic workspace, patients see it only in the labelled *reference* tier ("General information based on the sources listed below. The clinic's dermatologist has not reviewed this wording yet."), and that tier is **off by default when the app runs in production** (`COSMO_KB_REFERENCE=labelled` turns it on).

## What we need from you

1. For each article (Part A): read the exact text, check the statements listed under *Statements to verify* against the sources, then approve, edit or reject it in the clinic workspace (*Clinically reviewed knowledge base*). An approval is bound to the exact text (the hash below) and lapses on the re-review date. An edit returns the article to draft.
2. For each warning-sign rule (Part B): confirm, change or remove it. These decide when the assistant says *this should be looked at today* or *worth booking soon*.
3. Confirm that the way the sources are used is acceptable to the clinic (Part C).

Sign-off page: `SIGNOFF_TEMPLATE.md`. Spreadsheet versions: `derm_library_review.csv`, `derm_rules_review.csv`. How approval is recorded: `APPROVAL_PROCEDURE.md`.

## Part A: articles

### A1. Acne and pimples

`derm.acne`, topic `acne`, version 1, text hash that approval binds: `53e1f119cd592d2d…`

> Acne is a very common skin condition in which pores get blocked and inflamed. It causes spots such as blackheads, whiteheads, red bumps and sometimes deeper, painful lumps, most often on the face, chest and back. It usually responds to steady, gentle care, and a clinician can help when it does not.

**What it is**

- A pore (hair follicle) can become blocked by oil and dead skin cells. Bacteria that live on the skin can then add inflammation, which is why some spots turn red or hold pus.
- Common types are blackheads and whiteheads (blocked pores), red bumps (papules), pus-filled spots (pustules) and deep, firm, painful lumps (nodules or cysts).
- Acne can leave marks or scars, and it can affect how a person feels about themselves. That is a good enough reason to ask for help.

**What may contribute**

- Hormone changes, for example during puberty, around periods, in pregnancy and at menopause.
- Family history: acne often runs in families.
- Some medicines. A doctor or pharmacist can say whether a particular medicine might be involved; a prescribed medicine should not be stopped without their advice.
- Greasy or heavy skin, hair and make-up products that can block pores.
- Pressure or friction on the skin, such as from helmets, tight clothing or backpacks, and heat, sweat or humid weather.
- Picking, squeezing or scrubbing spots does not cause acne but can make it look worse and last longer.
- Dirty skin is not the cause. Blackheads look dark because of how the oil in the pore reacts to air, not because of dirt.
- Stress can make acne worse but is not thought to cause it. Whether certain foods play a part is still being studied, and the evidence is mixed.

**Gentle self-care**

- Wash gently up to twice a day, and after sweating, with a mild cleanser applied with clean fingertips. Rinse with lukewarm water and pat dry.
- Choose make-up, sunscreen, moisturiser and hair products labelled non-comedogenic or oil-free (made so they are less likely to block pores).
- Take make-up off before bed.
- Keep hair off the face, and wash oily hair regularly.
- If acne is on the body, wear loose, breathable clothes and change out of sweaty clothes soon after exercise.
- Protect the skin from the sun with shade, clothing and a broad-spectrum, water-resistant sunscreen of SPF 30 or higher that is labelled non-comedogenic.
- Spot treatments with ingredients such as salicylic acid or benzoyl peroxide are sold without a prescription. A pharmacist can help choose one and explain how to start gently, because they can dry and irritate the skin.
- Give a routine time. Improvement usually takes several weeks, and it is fairer to judge a treatment after about six to eight weeks than after a few days.
- Keep to one routine instead of changing products every week.

**What to avoid**

- Picking, popping or squeezing spots. This can push material deeper, slow healing and raise the chance of dark marks and scars.
- Scrubbing, rough washcloths or sponges, strong astringents, alcohol-based toners and harsh exfoliants. They dry and irritate the skin and can make acne worse.
- Washing the face many times a day.
- Sleeping in make-up, and sharing make-up, brushes or applicators.
- Heavy oils, pomades or thick creams on acne-prone areas such as the forehead and temples.
- Sunbeds and deliberate tanning. Tanning can make acne worse, and some acne treatments make skin more sensitive to the sun.

**How to keep track**

- Take a clear photo in the same light once a week, and note new products, stress, periods or illness. Looking at photos side by side is more reliable than checking the mirror every day, because change takes weeks.
- Note whether spots are getting deeper, more painful, or leaving marks.

**When to see a clinician**

- Self-care has not helped after about six to eight weeks.
- There are many spots, or spots that are deep, painful or leaving scars.
- Acne is affecting mood, sleep or confidence.
- Dark marks are slow to fade, or raised scars are forming. This is more likely in people with naturally darker skin after severe acne, so early advice helps.
- Acne appears suddenly and severely in adulthood, or comes together with other changes in the body such as very irregular periods.

**Signs that need prompt help**

- Skin around spots that is hot, swollen, increasingly red or very painful, or any fever, can mean an infection and needs prompt assessment. Redness can be harder to see on brown and black skin, so warmth, swelling and pain matter as much as colour.

**What a photo cannot show**

- A photo cannot tell blackheads from other small bumps, show how deep a spot is, or tell acne from conditions that look alike, such as rosacea, folliculitis or a reaction to a product.
- Lighting, filters and camera settings change how red or dark spots look.

**When this advice may not apply (shown with self-care advice)**

- Active ingredients in spot treatments can sting or dry the skin. If pregnant, planning a pregnancy, breastfeeding, or if the skin is very sensitive or has eczema, ask a doctor or pharmacist before starting any active ingredient.

**Limits the article states**

- General education, not a diagnosis. Acne-like spots can have other causes.
- Advice here is general. Strength, suitability and timing of treatments need an in-person assessment.

**Sources**

- NHS (England): Acne <https://www.nhs.uk/conditions/acne/> (page checked 2026-09-22; reuse basis `ogl_v3`)
- MedlinePlus, National Library of Medicine (USA): Acne <https://medlineplus.gov/acne.html> (page checked 2024-10-12; reuse basis `us_gov_public_domain`)
- National Institute of Arthritis and Musculoskeletal and Skin Diseases (NIAMS), NIH (USA): Acne <https://www.niams.nih.gov/health-topics/acne> (page checked 2023-07; reuse basis `us_gov_public_domain`)
- American Academy of Dermatology: Acne: Tips for managing <https://www.aad.org/public/diseases/acne/skin-care/tips> (page checked 2022-11-16; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: 10 skin care habits that can worsen acne <https://www.aad.org/public/diseases/acne/skin-care/habits-stop> (page checked date not shown; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: 10 tips for clearing acne in darker skin tones <https://www.aad.org/public/diseases/acne/diy/skin-color> (page checked date not shown; reuse basis `restricted_facts_only`)

**Statements to verify before approving**

- [ ] Washing up to twice a day with a mild cleanser, and after sweating, is appropriate for the clinic's patients.
- [ ] Judging an over-the-counter routine after about six to eight weeks is consistent with the clinic's practice.
- [ ] The statement that sudden severe acne in adults, or acne with very irregular periods, should be discussed with a clinician is appropriate and complete.
- [ ] Mentioning salicylic acid and benzoyl peroxide as products sold without a prescription is accurate for the countries the clinic serves.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A2. Dark marks and scars after acne

`derm.acne-marks-scars`, topic `acne-marks`, version 1, text hash that approval binds: `25ff4c5ac48f0f36…`

> After a pimple heals, a flat dark or red mark can remain. This is different from a scar, which changes the texture of the skin. Marks often fade slowly with sun protection and gentle care, while scars usually need a dermatologist's advice.

**What it is**

- A flat dark mark after a pimple is called post-inflammatory hyperpigmentation. It is the skin's reaction to inflammation and is common, particularly in deeper skin tones. It is not dirt and it is not a scar.
- A pink or red mark can also follow a pimple. Redness can be harder to see on brown and black skin.
- Scars change the texture of the skin: they can be pitted, or raised and firm. In people with darker skin, raised keloid-type scars and dark patches are more likely after severe acne.

**What may contribute**

- Picking or squeezing spots raises the chance of marks and scars.
- Sun exposure can make dark marks darker and slower to fade.
- Irritating products, especially ones that burn or sting, can darken marks.
- Acne that is left untreated for a long time or is severe is more likely to leave marks and scars.

**Gentle self-care**

- Treat the acne itself first. Marks usually start to fade once new spots stop forming.
- Wear a broad-spectrum, water-resistant sunscreen of SPF 30 or higher every day, and reapply about every two hours outdoors. A tinted sunscreen that contains iron oxide also protects against visible light, which can darken marks in deeper skin tones.
- Choose gentle, fragrance-free products, and stop any product that burns or stings.
- Be patient. Marks only a few shades darker than the surrounding skin often fade over about six to twelve months; deeper marks can take years.
- Some products sold without a prescription contain ingredients such as azelaic acid, vitamin C or retinoid-type ingredients that are used for dark marks. A pharmacist or dermatologist can say which are suitable for a given skin type before starting.

**What to avoid**

- Picking or squeezing spots.
- Skin-lightening creams of unknown content, including imported products without a clear label. Some have been found to contain mercury or hidden strong steroids, which can seriously harm the skin and health.
- Applying bleach or other household chemicals to the skin.
- At-home or salon peels, microneedling and laser treatments without advice. In deeper skin tones they can cause lasting dark or light patches.

**How to keep track**

- Photograph the marks in the same light every four weeks and note whether they look lighter, the same or darker.
- Note any new marks after a new product or a procedure.

**When to see a clinician**

- Marks are not fading after several months of sun protection and gentle care.
- Scars are forming, thickening or becoming raised, or they bother you.
- A dermatologist can choose and combine stronger treatments safely, including prescription creams and in-clinic procedures, and can say which suit your skin tone.

**What a photo cannot show**

- A photo has difficulty showing the difference between a flat mark and a slightly raised scar, and colours change with lighting and camera settings.

**When this advice may not apply (shown with self-care advice)**

- Ingredients used for dark marks can irritate skin and some are not suitable in pregnancy or breastfeeding. Ask a doctor or pharmacist before starting.

**Limits the article states**

- General education, not a diagnosis. A dark mark that is new, changing, itchy or bleeding should be looked at in person (see the article on moles and spots).
- Fading times are typical ranges, not a prediction for any one person.

**Sources**

- NHS (England): Acne <https://www.nhs.uk/conditions/acne/> (page checked 2026-09-22; reuse basis `ogl_v3`)
- American Academy of Dermatology: How to fade dark spots in darker skin tones <https://www.aad.org/public/everyday-care/skin-care-secrets/routine/fade-dark-spots> (page checked 2025-03-10; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: 10 tips for clearing acne in darker skin tones <https://www.aad.org/public/diseases/acne/diy/skin-color> (page checked date not shown; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: Melasma: Self-care <https://www.aad.org/public/diseases/a-z/melasma-self-care> (page checked 2023-09-12; reuse basis `restricted_facts_only`)
- US Food and Drug Administration: Skin Facts! What You Need to Know About Skin Lightening Products <https://www.fda.gov/consumers/health-fraud-scams/skin-facts-what-you-need-know-about-skin-lightening-products> (page checked 2026-05-11; reuse basis `us_gov_public_domain`)

**Statements to verify before approving**

- [ ] Marks a few shades darker than the skin usually fade within about six to twelve months, and deeper marks can take years.
- [ ] In deeper skin tones, peels, microneedling and lasers can cause lasting dark or light patches, so advice from a dermatologist experienced with the patient's skin tone should come first.
- [ ] The statement about raised keloid-type scars after severe acne in people with darker skin is accurate and appropriately worded.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A3. Dry skin and the skin barrier

`derm.dry-skin-barrier`, topic `dry-skin`, version 1, text hash that approval binds: `f665a9b0a19d395d…`

> Dry skin feels tight, rough or flaky because it is losing water faster than usual. Simple habits help most: short lukewarm showers, a gentle cleanser used only where needed, and moisturiser applied soon after washing and several times a day.

**What it is**

- The outer layer of the skin works as a barrier that holds water in and keeps irritants out. Hot water, harsh soaps, dry air and some products weaken it, and then the skin feels tight and flaky.
- Dryness can also go with eczema, psoriasis and other conditions, and occasionally it is a sign of an underlying health condition.

**What may contribute**

- Long or hot showers and baths.
- Harsh soaps and frequent washing.
- Dry air from heaters or air conditioning, and cold weather.
- Products with alcohol or fragrance, and some strong active ingredients.
- Age, and the skin condition eczema.

**Gentle self-care**

- Keep showers and baths to about five to ten minutes in warm, not hot, water, and pat the skin dry, leaving it slightly damp.
- Use a mild, fragrance-free cleanser, and only where needed. A leave-on moisturiser can replace soap for washing.
- Moisturise soon after washing and several times a day. Creams and ointments usually hold in water better than lotions; very dry skin often does best with an ointment.
- Choose products labelled fragrance-free. Unscented products can still contain ingredients that mask smell.
- A humidifier in the bedroom can help in dry air; clean it regularly.
- Wear gloves for wet tasks and in cold weather, and choose loose cotton clothing.
- Use sunscreen on exposed skin every day.

**What to avoid**

- Hot showers and long baths.
- Scrubs and harsh exfoliants.
- Alcohol-based and fragranced products on dry skin.
- Washing more often than needed.
- Aqueous cream as a moisturiser or soap substitute, because it can irritate the skin.

**How to keep track**

- Note how often moisturiser is applied and whether the skin feels better within about two weeks.

**When to see a clinician**

- The dryness continues after trying these steps for a couple of weeks.
- The skin is painful, cracked, bleeding, stinging or very itchy.
- Patches are red, thick or scaly, which can be eczema or psoriasis.
- A product causes burning, stinging or irritation.

**Signs that need prompt help**

- Oozing, crusting, pus, increasing redness or warmth, or a fever can mean an infection and need prompt assessment.

**What a photo cannot show**

- A photo cannot tell ordinary dryness from eczema, psoriasis or a fungal infection.

**Limits the article states**

- General education, not a diagnosis. Dryness that does not improve can point to another condition.
- Moisturisers can catch fire easily on clothing and bedding: keep them away from flames and cigarettes, and take cream from a tub with a clean spoon.

**Sources**

- American Academy of Dermatology: Dermatologists' top tips for relieving dry skin <https://www.aad.org/public/everyday-care/skin-care-basics/dry/dermatologists-tips-relieve-dry-skin> (page checked 2026-01-02; reuse basis `restricted_facts_only`)
- NHS (England): Emollients <https://www.nhs.uk/tests-and-treatments/emollients/> (page checked 2023-10-24; reuse basis `ogl_v3`)
- NHS (England): Atopic eczema <https://www.nhs.uk/conditions/atopic-eczema/> (page checked 2024-09-06; reuse basis `ogl_v3`)

**Statements to verify before approving**

- [ ] Showers of about five to ten minutes in warm water are appropriate advice for the clinic's patients and climate.
- [ ] Stating that ointments suit very dry skin better than lotions is appropriate, and the NHS advice that ointments should not be used on weeping eczema is respected elsewhere.
- [ ] The NHS emollients page's scheduled review date (24 October 2026) is close, so recheck the source wording.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A4. Eczema: itchy, dry, inflamed patches

`derm.eczema`, topic `eczema`, version 1, text hash that approval binds: `3e68ab3d89ec3585…`

> Eczema (atopic dermatitis) causes dry, itchy, red or darker patches that come and go. It is not contagious. Regular moisturising, avoiding triggers and not scratching help, and infected or severe eczema needs a clinician.

**What it is**

- Eczema is a common long-term skin condition that causes dry, itchy patches. It often starts in childhood but can begin at any age, and it can affect any part of the body.
- On brown and black skin, the patches may look darker brown, purple or grey rather than red.
- It is not contagious. Other conditions, such as psoriasis or a fungal infection, can look similar, so a patch that does not respond to moisturising deserves a clinician's look.

**What may contribute**

- Soap, washing detergent and other irritants.
- Some fabrics, such as wool, and heat or sweating.
- Pets, pollen and dust mites, and certain foods for some people. Diet should not be changed unless a doctor advises it.
- Dry air, cold weather and stress.

**Gentle self-care**

- Moisturise at least twice a day, and keep doing it when the skin looks better.
- Wash with a moisturiser used as a soap substitute instead of ordinary soap.
- Notice and avoid the triggers that seem to bring on flares.
- Keep nails short, and gently pat or rub an itchy area instead of scratching.
- Keep cool, and wear loose, soft clothing such as cotton.
- Take moisturiser from a tub with a clean spoon or use a pump, and do not share it.
- Moisturisers can catch fire easily on clothing and bedding, so keep them away from flames and cigarettes.

**What to avoid**

- Scratching.
- Known triggers such as perfumed products and harsh soaps.
- Overheating and very hot baths or showers.
- Aqueous cream as a moisturiser or soap substitute, because it can irritate the skin.

**How to keep track**

- Note what happens before flares (new products, weather, illness, stress) and take dated photos of patches.
- Note whether the itch disturbs sleep.

**When to see a clinician**

- The symptoms sound like eczema, or treatments are not helping.
- It is severe, or it affects sleep, school, work or mood.
- Prescription creams for flares should be used only as a clinician directs.

**Signs that need prompt help**

- Blistering, crusting, weeping fluid, pus-filled spots, pain, swelling or warmth, sudden worsening or spreading, a high temperature or feeling generally unwell. These can mean an infection or a more serious complication and need an urgent assessment.

**What a photo cannot show**

- A photo cannot tell eczema from psoriasis, a fungal infection or contact dermatitis, and it cannot show itch.

**Limits the article states**

- General education, not a diagnosis.

**Sources**

- NHS (England): Atopic eczema <https://www.nhs.uk/conditions/atopic-eczema/> (page checked 2024-09-06; reuse basis `ogl_v3`)
- NHS (England): Emollients <https://www.nhs.uk/tests-and-treatments/emollients/> (page checked 2023-10-24; reuse basis `ogl_v3`)
- American Academy of Dermatology: Dermatologists' top tips for relieving dry skin <https://www.aad.org/public/everyday-care/skin-care-basics/dry/dermatologists-tips-relieve-dry-skin> (page checked 2026-01-02; reuse basis `restricted_facts_only`)

**Statements to verify before approving**

- [ ] The statement that diet should not be changed unless a doctor advises it matches the clinic's practice.
- [ ] The urgent list (infection or a herpes complication) is complete and worded proportionately.
- [ ] Using a moisturiser as a soap substitute is practical for the clinic's patients, where leave-on emollient washes may not be easy to buy.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A5. Skincare habits that can worsen irritation

`derm.habits-worsen`, topic `habits`, version 1, text hash that approval binds: `c655c10821649e91…`

> Many skin problems get worse because of how the skin is treated, not because of dirt. The main culprits are scrubbing, washing too often, picking, hot water, changing products constantly and stacking strong ingredients. Gentle, consistent care usually works better.

**What it is**

- Skin that is over-washed, scrubbed or treated with many strong products loses its protective barrier and becomes dry, red and sore.
- Popping or squeezing spots can push material deeper, increase inflammation and leave marks or scars.

**What may contribute**

- Switching products every week or so, so nothing gets a fair trial. Many treatments need six to eight weeks, and clearing can take three to four months.
- Treating only the visible spot, when acne treatments are meant to be applied thinly across the whole acne-prone area.
- Using make-up, hair or skin products that block pores, especially hair oils or pomades that reach the forehead and temples.
- Sleeping in make-up, and sharing make-up, brushes or applicators.
- Washing the face many times a day, or scrubbing hard.
- Using very hot water.
- Rubbing off sweat during exercise instead of gently patting it away.
- Wearing caps and hats that are not washed often, because sweat and oil build up.

**Gentle self-care**

- Wash gently with fingertips, up to twice a day and after sweating, then pat dry.
- Pat sweat away with a clean towel.
- Moisturise with a product made for the skin type, and avoid drying alcohol-based toners.
- Give a routine or treatment a fair trial before changing it, unless it burns or stings.
- Remove make-up every night, and keep brushes and applicators personal.
- Wash caps, hats and pillowcases regularly.

**What to avoid**

- Picking, popping or squeezing spots.
- Home remedies that are strong or acidic on the skin, such as lemon juice, toothpaste or baking soda. They can burn the skin and leave dark marks. A clinician or pharmacist can suggest safer options.
- Skin-lightening creams of unknown content, and bleach or other household chemicals on the skin.
- Using leftover steroid or medicated creams on the face or an unknown rash without advice.
- Many new products at once.
- Sunbeds and deliberate tanning.

**How to keep track**

- Write down the date each product is started or stopped. If the skin gets worse after a change, that note points to the likely cause.

**When to see a clinician**

- The skin stays irritated after simplifying the routine for a couple of weeks.
- Deep or painful acne, or marks and scars forming.
- Any burn, blister or rash after a home remedy or a product.

**Signs that need prompt help**

- Spreading redness, pain, heat, swelling or pus, or a fever after a burn, a picked spot or a procedure.

**What a photo cannot show**

- A photo cannot show which habit is causing the problem.

**Limits the article states**

- General education, not a diagnosis. Which habit matters most differs from person to person.

**Sources**

- American Academy of Dermatology: 10 skin care habits that can worsen acne <https://www.aad.org/public/diseases/acne/skin-care/habits-stop> (page checked date not shown; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: Acne: Tips for managing <https://www.aad.org/public/diseases/acne/skin-care/tips> (page checked 2022-11-16; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: 10 tips for clearing acne in darker skin tones <https://www.aad.org/public/diseases/acne/diy/skin-color> (page checked date not shown; reuse basis `restricted_facts_only`)
- NHS (England): Acne <https://www.nhs.uk/conditions/acne/> (page checked 2026-09-22; reuse basis `ogl_v3`)
- American Academy of Dermatology: How to fade dark spots in darker skin tones <https://www.aad.org/public/everyday-care/skin-care-secrets/routine/fade-dark-spots> (page checked 2025-03-10; reuse basis `restricted_facts_only`)
- US Food and Drug Administration: Skin Facts! What You Need to Know About Skin Lightening Products <https://www.fda.gov/consumers/health-fraud-scams/skin-facts-what-you-need-know-about-skin-lightening-products> (page checked 2026-05-11; reuse basis `us_gov_public_domain`)
- American Academy of Dermatology: How to test skin care products <https://www.aad.org/public/everyday-care/skin-care-secrets/prevent-skin-problems/test-skin-care-products> (page checked 2021-08-10; reuse basis `restricted_facts_only`)

**Statements to verify before approving**

- [ ] The warning about lemon juice, toothpaste and baking soda is not taken from the listed pages; it is general dermatology advice and needs the clinic's confirmation or removal.
- [ ] The warning about leftover steroid or medicated creams on the face or on an unknown rash is general dermatology advice and needs the clinic's confirmation.
- [ ] Allowing three to four months for full clearing of acne is consistent with the clinic's practice.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A6. Common skincare products and ingredients

`derm.ingredients`, topic `ingredients`, version 1, text hash that approval binds: `a32e276789e95f8f…`

> Skincare products fall into a few groups. Knowing what each group is for makes it easier to choose gently and to avoid stacking several strong ingredients that irritate the skin. This is general education about product types, not a recommendation of any product or strength.

**What it is**

- Cleansers wash off dirt, oil and make-up. Mild, fragrance-free cleansers that do not strip the skin are the usual starting point. A leave-on moisturiser can replace soap for washing.
- Moisturisers and emollients hold water in the skin. Ointments are the thickest, then creams, then lotions.
- Sunscreens use either mineral filters (zinc oxide, titanium dioxide) or chemical filters. Both can work. Mineral products are often suggested for sensitive skin, and tinted ones add protection against visible light.
- Salicylic acid is an ingredient that helps clear blocked pores and is used for blackheads and whiteheads. It can dry or sting the skin.
- Benzoyl peroxide is an acne ingredient that reduces the bacteria linked to spots. It can dry and irritate the skin, and it can bleach towels, pillowcases and hair.
- Azelaic acid, vitamin C and kojic acid are used to help even out skin tone. They can sting.
- Retinoids are vitamin A-type ingredients used for acne, marks and skin ageing. Milder forms are sold without a prescription in some countries and stronger ones need a prescription. They can cause dryness and peeling and can make skin more sensitive to the sun.
- Exfoliating acids such as glycolic acid can smooth the skin but irritate it easily, and they can make skin more sensitive to the sun.

**Gentle self-care**

- Start with three basics: a gentle cleanser, a moisturiser and a sunscreen.
- Add one active ingredient at a time, a couple of weeks apart, and test it on a small patch first.
- Use sunscreen every day when using ingredients that make skin more sensitive to the sun.
- Check what is legal and sold without a prescription in the country where you live, and ask a pharmacist before starting a new active ingredient.
- Choose products with a clear label that lists the ingredients.

**What to avoid**

- Layering several strong active ingredients on the same day.
- Skin-lightening or 'fairness' creams with unknown contents or no proper label. Some contain mercury or hidden steroids.
- Using medicated or steroid creams left over from earlier treatments on the face or on a new rash without a clinician's advice.
- Products with handmade labels, non-English labels or no label when the ingredients cannot be checked.

**How to keep track**

- Keep a list of the products in use with the date each was started, and note any stinging, redness or dryness.

**When to see a clinician**

- A product causes burning, swelling or a lasting rash.
- The skin is not improving with simple over-the-counter care, or a stronger treatment is needed. A dermatologist can decide which prescription treatments suit the skin and the person.

**Signs that need prompt help**

- Swelling of the lips, tongue, face or throat, trouble breathing, or widespread hives after using a product need emergency help.

**What a photo cannot show**

- A photo cannot show which product suits a skin or which ingredient caused a reaction.

**When this advice may not apply (shown with self-care advice)**

- If pregnant, planning a pregnancy or breastfeeding, ask a doctor or pharmacist before using any active ingredient, and particularly any retinoid.
- Very sensitive skin, eczema, rosacea and a history of reactions to skincare call for extra care and a patch test.
- Some ingredients are not suitable for children. Ask a doctor or pharmacist.

**Limits the article states**

- General education about product types. No product, brand, strength or amount is recommended here, and nothing here replaces a clinician's advice.
- Which products are sold without a prescription differs between countries.

**Sources**

- NHS (England): Acne <https://www.nhs.uk/conditions/acne/> (page checked 2026-09-22; reuse basis `ogl_v3`)
- American Academy of Dermatology: How to fade dark spots in darker skin tones <https://www.aad.org/public/everyday-care/skin-care-secrets/routine/fade-dark-spots> (page checked 2025-03-10; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: How to test skin care products <https://www.aad.org/public/everyday-care/skin-care-secrets/prevent-skin-problems/test-skin-care-products> (page checked 2021-08-10; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: Sunscreen FAQs <https://www.aad.org/media/stats-sunscreen> (page checked 2025-02-11; reuse basis `restricted_facts_only`)
- NHS (England): Emollients <https://www.nhs.uk/tests-and-treatments/emollients/> (page checked 2023-10-24; reuse basis `ogl_v3`)
- American Academy of Dermatology: Dermatologists' top tips for relieving dry skin <https://www.aad.org/public/everyday-care/skin-care-basics/dry/dermatologists-tips-relieve-dry-skin> (page checked 2026-01-02; reuse basis `restricted_facts_only`)
- US Food and Drug Administration: Skin Facts! What You Need to Know About Skin Lightening Products <https://www.fda.gov/consumers/health-fraud-scams/skin-facts-what-you-need-know-about-skin-lightening-products> (page checked 2026-05-11; reuse basis `us_gov_public_domain`)
- American Academy of Dermatology: 10 tips for clearing acne in darker skin tones <https://www.aad.org/public/diseases/acne/diy/skin-color> (page checked date not shown; reuse basis `restricted_facts_only`)

**Statements to verify before approving**

- [ ] Naming salicylic acid, benzoyl peroxide, azelaic acid, vitamin C, kojic acid and retinoids as product types is acceptable, and the way each is described is accurate.
- [ ] The statement that milder retinoids are sold without a prescription in some countries is accurate, and the clinic's own rule for its region should replace it if different.
- [ ] The pregnancy and breastfeeding caution, particularly for retinoids, matches the clinic's policy.
- [ ] The warning about steroid creams left over from earlier treatments is appropriate and complete.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A7. Moles and spots that should be checked

`derm.moles-and-spots`, topic `moles`, version 1, text hash that approval binds: `cd740eb87379dc71…`

> Most moles are harmless, but a spot that is new, changing, bleeding or looks different from the others should be checked by a clinician. Finding skin cancer early makes it easier to treat. A photo cannot tell whether a spot is cancer; only an examination in person can.

**What it is**

- The ABCDE checks help notice changes: Asymmetry (the two halves do not match), Border (uneven, scalloped or blurred edge), Colour (more than one colour, such as tan, brown, black, red, white or blue), Diameter (often larger than about 6 millimetres, but they can be smaller), and Evolving (any change in size, shape or colour, or a spot that looks different from the others).
- Other warning signs include a mole or spot that is sore, swollen, itchy, bleeding or crusty, a sore that does not heal or heals and comes back, and a dark line under a fingernail or toenail that was not caused by an injury.
- Skin cancers can appear anywhere, including places that rarely see the sun. Rarer types can appear on the palms, soles, nails, eyes and genitals, so the whole skin should be looked at when checking.
- A skin cancer can be painless.

**What may contribute**

- Sun exposure and sunburn, particularly repeated burns, and the use of sunbeds.
- Many moles, fair skin, and a personal or family history of skin cancer.

**Gentle self-care**

- Check the whole skin regularly, using a mirror or a friend to see hard-to-reach places such as the back and scalp, and the palms, soles and nails.
- Photograph moles that are worth watching, with a coin or ruler beside them for size, so that changes are easier to see.
- Protect the skin from the sun with shade, clothing and a broad-spectrum sunscreen, and avoid sunbeds.

**What to avoid**

- Trying to remove, cut, burn or treat a mole or spot at home.
- Waiting to see whether a changing spot settles on its own.

**How to keep track**

- Compare each mole with the earlier photos every month or two, and note any change in size, shape, colour or feel.

**When to see a clinician**

- A mole has changed in size, shape or colour.
- A mole is painful, itchy, inflamed, bleeding or crusty.
- A new or unusual mark has not gone away after a few weeks, including on the palms or soles.
- There is a dark area under a nail that was not caused by an injury.
- A spot looks different from the others on the body.
- An appointment to check is a sensible step even when unsure. Being referred quickly does not mean cancer has been found.

**What a photo cannot show**

- A photo cannot tell whether a spot is cancer, and this app does not judge moles from photos. A clinician examines the spot, often with a special magnifier, and may take a sample.

**Limits the article states**

- General education, not a diagnosis. The ABCDE checks are prompts to get a spot examined, not a test, and a spot with none of these features can still need checking.

**Sources**

- NHS (England): Symptoms of melanoma skin cancer <https://www.nhs.uk/conditions/melanoma-skin-cancer/symptoms/> (page checked 2026-07-15; reuse basis `ogl_v3`)
- American Academy of Dermatology: What to look for: ABCDEs of melanoma <https://www.aad.org/public/diseases/skin-cancer/find/at-risk/abcdes> (page checked date not shown; reuse basis `restricted_facts_only`)
- MedlinePlus, National Library of Medicine (USA): Sun Exposure <https://medlineplus.gov/sunexposure.html> (page checked 2026-08-25; reuse basis `us_gov_public_domain`)

**Statements to verify before approving**

- [ ] The statement that skin cancers can be painless is accurate and appropriately worded.
- [ ] Taking photos with a coin or ruler beside a mole is acceptable advice for the clinic's patients.
- [ ] The sentence that a quick referral does not mean cancer has been found is appropriate.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A8. How to keep track of a skin concern and prepare for an appointment

`derm.monitoring`, topic `monitoring`, version 1, text hash that approval binds: `37a1a32623c25625…`

> A simple record helps you and your dermatologist see whether something is changing. Dated photos in the same light and a few notes are usually enough.

**What it is**

- Skin changes slowly. Dated photos and a few notes show whether a concern is improving, staying the same or getting worse, and they help a clinician who sees the skin only once.
- Photos are a record, not a diagnosis.

**Gentle self-care**

- Take photos of the same area in the same light (a window in daylight works well), at the same distance and angle, without filters.
- Take them at regular times, for example weekly for acne and monthly for marks or patches.
- For a spot or mole, place a coin or ruler next to it so the size is clear.
- Write down the date a concern started, what it looked like first, and what has changed.
- Note symptoms such as itch, pain, burning, bleeding or swelling, and rate them out of ten if useful.
- List the products used on the area, including medicines, with the start date of each, and note any earlier reactions or allergies.
- Note what seems to make it better or worse, such as heat, sweat, stress, periods, foods or new products.
- Before an appointment, write down the questions that matter most and bring the product list and photos.

**What to avoid**

- Using filters or editing the photos.
- Changing many things at once, which makes the record hard to read.

**How to keep track**

- Review the photos every two weeks to see the trend.
- As a guide, ask for advice if acne has not improved after about six to eight weeks, or if an irritated area has not settled within a week or two of removing likely triggers.

**When to see a clinician**

- Something is getting worse, spreading, painful or bleeding.
- A mole or spot is changing in size, shape or colour.
- There is no improvement over the time frames above.

**Signs that need prompt help**

- Rapid spreading, a fever, severe pain or swelling of the face, lips or tongue need prompt help and should not wait for a photo record.

**What a photo cannot show**

- Photos taken in different light look different even when the skin has not changed, so keep the light as consistent as possible.

**Limits the article states**

- General education. A record supports an examination and does not replace one.

**Sources**

- NHS (England): Symptoms of melanoma skin cancer <https://www.nhs.uk/conditions/melanoma-skin-cancer/symptoms/> (page checked 2026-07-15; reuse basis `ogl_v3`)
- American Academy of Dermatology: What to look for: ABCDEs of melanoma <https://www.aad.org/public/diseases/skin-cancer/find/at-risk/abcdes> (page checked date not shown; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: Acne: Tips for managing <https://www.aad.org/public/diseases/acne/skin-care/tips> (page checked 2022-11-16; reuse basis `restricted_facts_only`)

**Statements to verify before approving**

- [ ] Placing a coin or ruler next to a spot is common practice but is not taken from the listed pages; the clinic should confirm or replace it.
- [ ] The suggested photo intervals (weekly for acne, monthly for marks or patches) are practical suggestions for the clinic to confirm.
- [ ] The time frames for seeking advice are consistent with the clinic's practice.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A9. What a photo can and cannot show

`derm.photo-limits`, topic `photo-limits`, version 1, text hash that approval binds: `7f225cc0890f7450…`

> A photo is useful as a record and for a clinician who cannot see the skin in person, but it shows only part of the picture. It cannot show depth, texture, warmth or tenderness, and it cannot say how long something has been there. How well a photo reflects the skin also depends on light, camera and skin tone.

**What it is**

- A photo can show the general look, colour and spread of a skin concern at one moment, and it can help show change over time.
- A photo cannot show how deep a spot is, how the skin feels, whether it is warm or tender, or whether it itches. It cannot tell an infection from an inflammation, or a harmless spot from a skin cancer.
- No fixed rule or app can name a skin condition reliably from a single photo, and a photo alone cannot establish vitamin deficiencies, hormone imbalances or internal disease.
- Colour changes depend on skin tone. Redness can be harder to see on brown and black skin, where inflammation can look darker brown, purple or grey. Sunburn on darker skin can look like darker patches.

**What may contribute**

- Light: daylight from a window gives the truest colour; indoor lamps, flash and shadows change how skin looks.
- Camera settings, filters, beauty modes and image compression.
- Distance and angle, which change size and shape.
- Make-up, sweat, oil and shine on the skin.

**Gentle self-care**

- Take photos in soft daylight, without flash, filters or beauty modes, with the area in focus and filling the frame.
- Use the same place, light and angle each time so that photos can be compared.
- Add a note about how long the concern has been there and any symptoms, because the photo cannot show these.

**What to avoid**

- Relying on a photo to decide whether something is serious.
- Editing or filtering photos meant for comparison or for a clinician.

**How to keep track**

- Keep the photos with the date and a short note so that a clinician can see the history.

**When to see a clinician**

- Any concern that is painful, spreading, bleeding, changing or lasting more than a few weeks, however it looks in a photo.
- Any time the photo looks fine but the skin feels very different from normal.

**What a photo cannot show**

- Tools that analyse skin photographs are usually trained and tested on far more photos of lighter skin than of darker skin, so their results can be less reliable on darker skin. This app does not analyse skin conditions from photos at all: it checks the quality of the photo and the area that was photographed.

**Limits the article states**

- General education about the limits of photographs.

**Sources**

- NHS (England): Acne <https://www.nhs.uk/conditions/acne/> (page checked 2026-09-22; reuse basis `ogl_v3`)
- NHS (England): Rosacea <https://www.nhs.uk/conditions/rosacea/> (page checked 2023-03-17; reuse basis `ogl_v3`; **source re-check overdue**)
- NHS (England): Contact dermatitis <https://www.nhs.uk/conditions/contact-dermatitis/> (page checked 2023-05-03; reuse basis `ogl_v3`; **source re-check overdue**)
- NHS (England): Cellulitis <https://www.nhs.uk/conditions/cellulitis/> (page checked 2024-04-18; reuse basis `ogl_v3`)
- NHS (England): Meningitis: symptoms <https://www.nhs.uk/conditions/meningitis/symptoms/> (page checked 2026-06-12; reuse basis `ogl_v3`)
- American Academy of Dermatology: Sunscreen FAQs <https://www.aad.org/media/stats-sunscreen> (page checked 2025-02-11; reuse basis `restricted_facts_only`)

**Statements to verify before approving**

- [ ] The sentence that tools for analysing skin photographs are usually trained on far more photos of lighter skin is a general statement about the field and needs a reference or the clinic's confirmation.
- [ ] The statement that a photo cannot establish vitamin deficiencies, hormone imbalances or internal disease is appropriate.
- [ ] The photo-taking advice (daylight, no flash or filters) is appropriate and consistent with the clinic's own photo guide.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A10. Uneven skin tone and dark patches

`derm.pigmentation`, topic `pigmentation`, version 1, text hash that approval binds: `3e9f2215a615d3f5…`

> Skin colour comes from a pigment called melanin. Dark patches can follow acne or irritation, appear with sun exposure or hormone changes, or be linked to other conditions. Daily sun protection is the most useful first step, and a dermatologist can tell which type of patch is present.

**What it is**

- Pigmentation changes happen when the skin makes more or less melanin. Too much makes a patch darker; too little makes it lighter.
- Common reasons for darker patches include sun exposure, pregnancy, skin injury or inflammation (for example a healed pimple), some medicines, and irritating skin or hair products.
- Melasma is a common pattern of brown or grey-brown patches, usually on the face, which sun and hormone changes often make worse.
- Some conditions cause lighter patches instead, such as vitiligo. These need a clinician's assessment.

**What may contribute**

- Sunlight, including the visible light that comes through windows and on cloudy days.
- Hormone changes, such as pregnancy.
- Inflammation or injury to the skin, including acne and itching or rubbing.
- Irritating products that burn or sting.
- Some medicines. A prescribed medicine should not be stopped without talking to the prescriber, who may be able to suggest another.

**Gentle self-care**

- Protect the skin from the sun every day, including cloudy days: shade, a wide-brimmed hat, sunglasses and a broad-spectrum, water-resistant sunscreen of SPF 30 or higher, reapplied about every two hours outdoors.
- A tinted sunscreen that contains iron oxide protects against visible light, which can worsen some dark patches, especially in deeper skin tones.
- Choose gentle, fragrance-free skincare, and stop any product that burns or stings, because irritation can darken patches.
- Be patient. Patches usually take months to fade, and some need a dermatologist's treatment. Make-up can even out the tone in the meantime.
- Some products sold without a prescription contain ingredients such as azelaic acid, vitamin C or retinoid-type ingredients that are used for dark patches. A pharmacist or dermatologist can say which suit a given skin.

**What to avoid**

- Sunbeds and sunlamps. Indoor tanning can worsen dark patches and get in the way of treatment.
- Skin-lightening or fairness creams of unknown content. Some imported or unlabelled products contain mercury or hidden strong steroids, which can thin and permanently discolour the skin and harm health.
- Household bleach or similar chemicals on the skin.
- Rubbing, scrubbing or picking at the patches.

**How to keep track**

- Photograph the area in the same light once a month.
- Note whether patches are spreading, new, or changing in colour or edge. A single spot that is changing needs a check (see the article on moles and spots).

**When to see a clinician**

- Patches bother you or do not go away. A dermatologist can tell whether they are melasma, sun spots, marks after inflammation or something else, because other conditions can also cause uneven skin tone.
- Patches are new, spreading quickly, or look different from each other.
- A single dark spot or mole is changing, itchy, bleeding or has uneven colours or edges.
- You would like to discuss prescription creams or in-clinic treatments.

**What a photo cannot show**

- A photo cannot reliably tell sun spots, melasma and marks after acne apart. Lighting and colour balance change how dark a patch looks.
- On darker skin, patches can look brown, grey or blue-grey.

**When this advice may not apply (shown with self-care advice)**

- Ingredients used for dark patches can irritate skin and some are not suitable in pregnancy or breastfeeding. Ask a doctor or pharmacist before starting.

**Limits the article states**

- General education, not a diagnosis. The cause of a dark patch needs an in-person assessment.
- Treatment times and results differ widely between people.

**Sources**

- MedlinePlus, National Library of Medicine (USA): Skin Pigmentation Disorders <https://medlineplus.gov/skinpigmentationdisorders.html> (page checked 2025-09-19; reuse basis `us_gov_public_domain`)
- American Academy of Dermatology: Melasma: Self-care <https://www.aad.org/public/diseases/a-z/melasma-self-care> (page checked 2023-09-12; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: How to fade dark spots in darker skin tones <https://www.aad.org/public/everyday-care/skin-care-secrets/routine/fade-dark-spots> (page checked 2025-03-10; reuse basis `restricted_facts_only`)
- US Food and Drug Administration: Skin Facts! What You Need to Know About Skin Lightening Products <https://www.fda.gov/consumers/health-fraud-scams/skin-facts-what-you-need-know-about-skin-lightening-products> (page checked 2026-05-11; reuse basis `us_gov_public_domain`)
- NHS (England): Sunscreen and sun safety <https://www.nhs.uk/live-well/seasonal-health/sunscreen-and-sun-safety/> (page checked 2026-03-24; reuse basis `ogl_v3`)

**Statements to verify before approving**

- [ ] Sun exposure and hormone changes (including pregnancy) are the most common triggers of melasma, and the statement matches the clinic's view.
- [ ] Visible light can worsen dark patches and a tinted, iron-oxide sunscreen is appropriate advice for the clinic's patients.
- [ ] The warning about skin-lightening products with mercury or hidden steroids is accurate for the products sold in the clinic's region.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A11. Reactions to skincare products and how to test a new one

`derm.product-reactions`, topic `product-reactions`, version 1, text hash that approval binds: `15e072d7ae8bdf2c…`

> Skin can react to a product the first time or after months of use. Testing a new product on a small patch and adding only one new product at a time makes it much easier to find the cause of a reaction.

**What it is**

- Some ingredients, such as retinol and glycolic acid, can cause temporary stinging or dryness, especially on sensitive skin. This is common and usually settles.
- An allergic reaction is different: it can cause redness, itching, swelling or blisters, and it usually does not settle while the product is still being used.
- The label words matter: fragrance-free means no fragrance ingredients were added, while unscented products can still contain ingredients that mask smell.

**What may contribute**

- Fragrance and essential oils.
- Preservatives and other additives.
- Using several new products at once, which makes the cause hard to find.

**Gentle self-care**

- Test a new leave-on product on a small patch, about the size of a coin, on the inner arm or inner elbow where it will not be rubbed or washed off. Use the amount normally used, once or twice a day for about seven to ten days.
- For a product that is rinsed off, such as a cleanser, leave it on the test patch for about five minutes, or as long as the label says.
- If there is no redness, itching or swelling after the test, the product is more likely to suit the skin on the face too, though a test cannot rule out every reaction.
- If a reaction starts, wash the product off gently and stop using it. A cool compress or plain petroleum jelly can soothe the skin.
- Introduce one new product at a time, a couple of weeks apart.
- Keep a note of the products and ingredients that caused trouble, and look for fragrance-free labels.

**What to avoid**

- Starting several new products on the same day.
- Skipping the patch test for a product with strong active ingredients.

**How to keep track**

- Photograph the test patch on day one, day three and at the end of the test.

**When to see a clinician**

- The reaction is severe, or does not improve with a cool compress or petroleum jelly.
- The ingredient that causes the reaction cannot be found. A dermatologist can arrange patch testing.

**Signs that need prompt help**

- Swelling of the lips, tongue, face or throat, trouble breathing, or hives spreading over the body need emergency help.

**What a photo cannot show**

- A photo cannot tell an irritation from an allergy.

**When this advice may not apply (shown with self-care advice)**

- A home patch test is not suitable if there has ever been a severe or swelling reaction to a product or to a similar product. Ask a clinician first.

**Limits the article states**

- General education, not a diagnosis. A home patch test does not rule out every reaction.

**Sources**

- American Academy of Dermatology: How to test skin care products <https://www.aad.org/public/everyday-care/skin-care-secrets/prevent-skin-problems/test-skin-care-products> (page checked 2021-08-10; reuse basis `restricted_facts_only`)
- NHS (England): Contact dermatitis <https://www.nhs.uk/conditions/contact-dermatitis/> (page checked 2023-05-03; reuse basis `ogl_v3`; **source re-check overdue**)

**Statements to verify before approving**

- [ ] A seven to ten day home patch test of the amount normally used is appropriate for the clinic's patients.
- [ ] Stating that a negative test makes suitability on the face more likely is appropriately cautious.
- [ ] The statement that stinging from retinol or glycolic acid is common and usually settles is appropriate.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A12. Red, sore or irritated skin

`derm.redness-irritation`, topic `redness`, version 1, text hash that approval binds: `72f83830fd20c246…`

> Red, sore, itchy or stinging skin has many possible causes: irritation from products or weather, an allergic reaction, eczema, rosacea, acne or sunburn. Often the first steps are to simplify the routine and avoid the likely trigger. Redness can be harder to see on brown and black skin, so how the skin feels matters as much as its colour.

**What it is**

- Irritation is the most common reason. Soaps, detergents, some skincare ingredients and frequent contact with water can irritate skin, and it often improves once the trigger is found and avoided.
- Allergic reactions are less common. Fragrances, preservatives, nickel and some cosmetics can cause them.
- Other common causes include eczema, rosacea, acne and sunburn.
- On brown or black skin, redness may look dark brown, purple or grey rather than red. Sunburn on darker skin can look like darker patches.

**What may contribute**

- A new product, or several new products at once.
- Fragrance, essential oils and alcohol-based products.
- Scrubbing, strong exfoliating acids or using many active ingredients together.
- Hot water, wind, cold, heat and sweat.
- Sun exposure.
- Stress, for some people.

**Gentle self-care**

- Stop any product started recently and rinse the area with lukewarm water.
- Keep to a short, bland routine for a week or two: a gentle fragrance-free cleanser, a plain moisturiser and a daily sunscreen. Mineral sunscreens are often recommended for sensitive skin.
- A cool compress can soothe the skin.
- Moisturise often, especially dry or itchy areas.
- Add products back one at a time, a couple of weeks apart, and test each on a small patch first.

**What to avoid**

- Fragrance, essential oils, alcohol-based toners, scrubs and strong exfoliating acids while the skin is irritated.
- Hot water on the face and body.
- Rubbing and scratching.
- Using several new active ingredients at the same time.

**How to keep track**

- Note when the redness started, what was applied just before, and whether it eases within a few days once the suspected trigger is removed.
- Take a photo in the same light every few days.

**When to see a clinician**

- The skin is not improving within about one to two weeks after removing likely triggers.
- It keeps coming back, or the trigger cannot be found. A dermatologist can arrange patch testing to look for allergies.
- It is severe, very uncomfortable, or affecting sleep or daily life.

**Signs that need prompt help**

- Swelling of the lips, tongue, face or throat, or trouble breathing.
- Redness that is spreading quickly, hot, swollen, very painful or oozing pus, or comes with a fever.
- A red, painful eye, light sensitivity or blurred vision.

**What a photo cannot show**

- Redness is harder to see in a photo on brown and black skin, and photos cannot show warmth, tenderness or itch.
- A photo cannot tell irritation from an allergy, eczema, rosacea or an infection.

**Limits the article states**

- General education, not a diagnosis. Red skin can have many causes, including infections.

**Sources**

- NHS (England): Contact dermatitis <https://www.nhs.uk/conditions/contact-dermatitis/> (page checked 2023-05-03; reuse basis `ogl_v3`; **source re-check overdue**)
- NHS (England): Rosacea <https://www.nhs.uk/conditions/rosacea/> (page checked 2023-03-17; reuse basis `ogl_v3`; **source re-check overdue**)
- American Academy of Dermatology: How to test skin care products <https://www.aad.org/public/everyday-care/skin-care-secrets/prevent-skin-problems/test-skin-care-products> (page checked 2021-08-10; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: Sunscreen FAQs <https://www.aad.org/media/stats-sunscreen> (page checked 2025-02-11; reuse basis `restricted_facts_only`)
- NHS (England): Emollients <https://www.nhs.uk/tests-and-treatments/emollients/> (page checked 2023-10-24; reuse basis `ogl_v3`)

**Statements to verify before approving**

- [ ] Keeping to a bland routine for one to two weeks and then adding products back one at a time is appropriate advice for the clinic's patients.
- [ ] Mineral sunscreens are a reasonable suggestion for irritated or sensitive skin.
- [ ] The urgent list is complete and worded proportionately.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A13. Rosacea and facial flushing

`derm.rosacea`, topic `rosacea`, version 1, text hash that approval binds: `81119002b1527d86…`

> Rosacea is a long-term skin condition that causes redness and flushing, mainly on the face, sometimes with small bumps and tiny visible blood vessels. It cannot be cured but can usually be controlled. Sun protection and avoiding triggers help, and a clinician can offer treatments.

**What it is**

- Typical features are redness across the nose, cheeks, forehead or chin that comes and goes, a feeling of warmth or heat in the face, and burning or stinging when water or skincare products touch the skin.
- Over time the redness can become persistent, tiny blood vessels can show, and small red or pink bumps can appear.
- Redness can be harder to see on brown and black skin, so burning, warmth and bumps are useful clues.

**What may contribute**

- Alcohol, spicy food and hot drinks.
- Sunlight, heat, humidity and cold weather.
- Exercise that raises the body temperature.
- Stress.

**Gentle self-care**

- Notice and avoid the triggers that bring on flushing.
- Choose gentle products made for sensitive skin.
- Wear a broad-spectrum sunscreen of SPF 30 or higher every day.
- Cover the face in cold weather and avoid overheating.
- If the eyelids are sore or crusty, clean them daily with a method a clinician or pharmacist recommends.
- Look after stress and sleep where possible.

**What to avoid**

- Known triggers where possible.
- Harsh cleansers, scrubs and products that sting.

**How to keep track**

- A simple diary of food, drink, weather, activity and products next to dated photos shows which triggers matter.

**When to see a clinician**

- The symptoms sound like rosacea. Treatments such as prescription creams or gels exist, and rosacea may get worse without treatment. This is not an emergency, but earlier advice helps.
- Treatment is not working. A dermatologist can review it.

**Signs that need prompt help**

- Eye pain, blurred vision, light sensitivity, a red eye or a gritty feeling can be a sign of an eye inflammation that needs prompt treatment.

**What a photo cannot show**

- A photo cannot tell rosacea from acne, eczema, a reaction to a product or other causes of facial redness.

**Limits the article states**

- General education, not a diagnosis. Rosacea is diagnosed by a clinician.

**Sources**

- NHS (England): Rosacea <https://www.nhs.uk/conditions/rosacea/> (page checked 2023-03-17; reuse basis `ogl_v3`; **source re-check overdue**)

**Statements to verify before approving**

- [ ] The source page's scheduled review date (March 2026) has passed, so the current NHS wording should be rechecked.
- [ ] Cleaning the eyelids daily when they are sore is appropriate to mention here, or should be left to the clinician.
- [ ] The urgent eye-symptom statement is appropriately worded.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A14. A simple, gentle skincare routine

`derm.routine-basics`, topic `routine`, version 1, text hash that approval binds: `bf9dd171a5f680ef…`

> A basic routine has three steps in the morning (gentle cleanse or rinse, moisturise, sunscreen) and two in the evening (gentle cleanse, moisturise). Adding products slowly, one at a time, makes it easy to tell what helps and what irritates.

**What it is**

- Skin types are a rough guide only: dry (tight or flaky), oily (shiny, blocked pores), combination, and sensitive (stings or reddens easily). Skin can change with season, age, hormones and products.
- Most skin concerns respond better to a short, consistent routine than to many products.

**Gentle self-care**

- Morning: rinse or cleanse gently if needed, moisturise, then apply a broad-spectrum sunscreen of SPF 30 or higher.
- Evening: take off sunscreen and make-up, cleanse gently with fingertips and lukewarm water, pat dry, and moisturise.
- Wash up to twice a day and after sweating; more often is not better.
- Choose fragrance-free products. If the skin is acne-prone, choose non-comedogenic or oil-free products.
- Dry skin does best with a richer cream or ointment; oily skin often prefers a lighter gel or lotion.
- Add one new product at a time, a couple of weeks apart, and test it on a small patch first.
- Give each product several weeks before judging it, unless it burns or stings, in which case stop it.
- Keep it simple. Every extra product, especially strong active ingredients, raises the chance of irritation.

**What to avoid**

- Scrubbing and strong exfoliating acids used often.
- Very hot water.
- Using several strong active ingredients at once.
- Sharing products or applicators.

**How to keep track**

- Take a photo in the same light once a week for the first month of a new routine and note any changes.

**When to see a clinician**

- A concern is not improving after about six to eight weeks, is painful, spreading or leaving marks.
- A routine always stings or causes redness.

**What a photo cannot show**

- A photo cannot show skin type, and it cannot tell whether a product suits a person.

**When this advice may not apply (shown with self-care advice)**

- If pregnant, planning a pregnancy or breastfeeding, ask a doctor or pharmacist before adding any active ingredient to the routine.

**Limits the article states**

- General education. A routine that suits most skin may not suit skin with eczema, rosacea or other conditions; those need their own advice.

**Sources**

- NHS (England): Acne <https://www.nhs.uk/conditions/acne/> (page checked 2026-09-22; reuse basis `ogl_v3`)
- American Academy of Dermatology: Acne: Tips for managing <https://www.aad.org/public/diseases/acne/skin-care/tips> (page checked 2022-11-16; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: Dermatologists' top tips for relieving dry skin <https://www.aad.org/public/everyday-care/skin-care-basics/dry/dermatologists-tips-relieve-dry-skin> (page checked 2026-01-02; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: How to test skin care products <https://www.aad.org/public/everyday-care/skin-care-secrets/prevent-skin-problems/test-skin-care-products> (page checked 2021-08-10; reuse basis `restricted_facts_only`)
- NHS (England): Emollients <https://www.nhs.uk/tests-and-treatments/emollients/> (page checked 2023-10-24; reuse basis `ogl_v3`)
- NHS (England): Sunscreen and sun safety <https://www.nhs.uk/live-well/seasonal-health/sunscreen-and-sun-safety/> (page checked 2026-03-24; reuse basis `ogl_v3`)
- American Academy of Dermatology: Sunscreen FAQs <https://www.aad.org/media/stats-sunscreen> (page checked 2025-02-11; reuse basis `restricted_facts_only`)

**Statements to verify before approving**

- [ ] The skin-type descriptions are general dermatology education and are not taken from one source: the clinic should confirm or replace them.
- [ ] Matching richer creams to dry skin and lighter gels to oily skin is appropriate general advice.
- [ ] Giving each new product several weeks is consistent with the clinic's practice.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A15. Sunscreen and sun protection

`derm.sun-protection`, topic `sun-protection`, version 1, text hash that approval binds: `b3a4265dc0d35816…`

> Daily sun protection is one of the most useful steps for healthy skin: it helps prevent sunburn, dark marks and skin cancer. It combines shade, clothing and a broad-spectrum sunscreen of SPF 30 or higher, applied generously and reapplied.

**What it is**

- UV rays reach the skin on cloudy and cool days as well as sunny ones, and they reflect off water, sand and concrete. They are strongest in the middle of the day.
- UVB rays mainly cause burning and UVA rays mainly cause ageing. SPF mostly describes UVB protection, so a sunscreen also needs to protect against UVA. A product labelled broad spectrum does both.
- A tan is not a sign of health. It shows the skin has already been damaged by UV rays.
- People with darker skin have some natural protection but can still burn and develop skin cancer. Sunburn on darker skin may look like darker patches instead of redness.

**What may contribute**

- Visible light, including light through windows, can darken some pigmentation such as melasma. Tinted sunscreens that contain iron oxide give extra protection against it.

**Gentle self-care**

- Choose a broad-spectrum, water-resistant sunscreen of SPF 30 or higher. Where a UVA star rating is shown, a rating of four or five stars (or the UVA logo in a circle) shows good UVA protection.
- Apply enough. As a guide for adults, about a teaspoon covers the face and about a shot glass covers the whole body. Many people use too little.
- Apply before going out, and do not forget the ears, neck, temples and the back of the neck.
- Reapply about every two hours when outdoors, and straight after swimming, sweating or towel drying. No sunscreen is waterproof.
- Add shade, a wide-brimmed hat, close-woven clothing that covers the arms and legs, and sunglasses that block UVA and UVB.
- Mineral sunscreens (zinc oxide or titanium dioxide) are often suggested for sensitive skin.
- For acne-prone skin, choose a sunscreen labelled non-comedogenic.
- Check the expiry date. If there is none, throw the sunscreen away after about three years, or sooner if it has been kept in the heat.
- Babies under six months should be kept out of strong sun and covered with clothing and shade, because sunscreen is generally not recommended at that age.

**What to avoid**

- Sunbeds and sunlamps.
- Relying on sunscreen alone. It works best together with shade and clothing.
- Deliberate tanning.

**How to keep track**

- Check the skin regularly, including areas that are rarely exposed to the sun, and photograph any moles or spots so that changes are easy to see.

**When to see a clinician**

- A mole or spot is changing, bleeding, itchy or looks different from the others (see the article on moles and spots).
- Sunburn is frequent or severe.
- A concern about vitamin D. Sun protection and vitamin D need balancing, and a clinician or pharmacist can advise.

**What a photo cannot show**

- A photo cannot show sun damage under the surface or tell whether a spot is harmless.

**When this advice may not apply (shown with self-care advice)**

- Sunscreen is generally not recommended for babies under six months. Ask a clinician about protection for infants.

**Limits the article states**

- General education. Sunscreen reduces but does not remove the risk from UV exposure. Rules on SPF and UVA labelling differ between countries.

**Sources**

- NHS (England): Sunscreen and sun safety <https://www.nhs.uk/live-well/seasonal-health/sunscreen-and-sun-safety/> (page checked 2026-03-24; reuse basis `ogl_v3`)
- American Academy of Dermatology: Sunscreen FAQs <https://www.aad.org/media/stats-sunscreen> (page checked 2025-02-11; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: How to apply sunscreen <https://www.aad.org/public/everyday-care/sun-protection/shade-clothing-sunscreen/how-to-apply-sunscreen> (page checked 2025-08-15; reuse basis `restricted_facts_only`)
- Centers for Disease Control and Prevention (USA): Sun Safety Facts <https://www.cdc.gov/skin-cancer/sun-safety/index.html> (page checked 2026-06-17; reuse basis `us_gov_public_domain`)
- MedlinePlus, National Library of Medicine (USA): Sun Exposure <https://medlineplus.gov/sunexposure.html> (page checked 2026-08-25; reuse basis `us_gov_public_domain`)
- American Academy of Dermatology: Melasma: Self-care <https://www.aad.org/public/diseases/a-z/melasma-self-care> (page checked 2023-09-12; reuse basis `restricted_facts_only`)

**Statements to verify before approving**

- [ ] SPF 30 or higher is the right minimum for the clinic's patients (the CDC and MedlinePlus pages quote SPF 15 as a minimum).
- [ ] The UVA star rating applies to UK products; check which labelling applies to products sold where the clinic works.
- [ ] A teaspoon for the face and a shot glass for the body is the right guide to quote.
- [ ] The advice on babies under six months is appropriate.
- [ ] The note on vitamin D is acceptable, or should be replaced by the clinic's own advice.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A16. Skin warning signs that need prompt help

`derm.warning-signs`, topic `warning-signs`, version 1, text hash that approval binds: `5a5fc6b267525c11…`

> Most skin problems are not emergencies, but some signs mean the skin is infected, a reaction is serious, or another illness is involved. Those signs should not wait for an ordinary appointment. This page explains the main ones in plain language.

**What it is**

- Skin infection (cellulitis): skin that is sore, hot and puffy, often red, and sometimes blistered, with a feeling of being unwell. Redness is less obvious on brown and black skin, so warmth, swelling and pain matter. Early treatment can stop it becoming more serious.
- A serious allergic reaction (anaphylaxis) develops within minutes: swelling of the lips, mouth, tongue or throat, trouble breathing or swallowing, feeling faint or confused, pale or blue skin, and sometimes an itchy raised rash.
- A serious drug or skin reaction can start with flu-like symptoms, then a spreading painful rash with blisters and sores in the mouth, eyes or genitals, and skin that peels easily.
- A rash that does not fade when a clear glass is pressed firmly against it, especially with fever, headache, a stiff neck, drowsiness or feeling very unwell, can be a sign of a serious blood or brain infection. Rashes are harder to see on brown and black skin, so look at paler areas such as the palms, soles, inside of the mouth and whites of the eyes.
- Shingles can cause a painful blistering rash on one side of the body. A rash on or near the eye, or any change in vision, needs urgent attention.
- Eczema that becomes infected can blister, crust, weep fluid and form pus-filled spots, with pain, warmth and sometimes fever.

**Gentle self-care**

- Do not wait or try home remedies when these signs are present: seek help promptly.
- While arranging help, note when the symptoms began and which medicines have been taken, and bring the medicines along.

**What to avoid**

- Driving yourself when seriously unwell: ask someone to take you instead.
- Waiting to see whether a spreading, painful or blistering rash settles on its own.

**How to keep track**

- A pen mark around the edge of a red area makes it easy to see whether it is spreading. Spreading, or increasing pain, is a reason to be seen the same day.

**When to see a clinician**

- Skin that is painful, hot and swollen: ask for an urgent appointment, or contact the local urgent medical helpline.
- A shingles rash on or near the eye or nose, or any change in vision: urgent assessment the same day.
- Eczema with blistering, crusting, weeping, pus or sudden worsening, or with a high temperature or feeling unwell: urgent assessment the same day.

**Signs that need prompt help**

- Emergency help now: swelling of the lips, mouth, tongue or throat, trouble breathing or swallowing; a rash that does not fade under a glass together with fever or feeling very unwell; a spreading, painful, blistering or peeling rash, or sores in the mouth or eyes; a skin infection with confusion, fainting, a very fast heartbeat, purple patches or cold, clammy or pale skin.

**What a photo cannot show**

- A photo cannot judge how serious a reaction or infection is. Symptoms such as pain, fever, spread and feeling unwell matter more than how the skin looks in a picture.

**Limits the article states**

- General education, not a triage service. This page cannot cover every situation, and anyone who is worried or feels very unwell should seek help without waiting.

**Sources**

- NHS (England): Cellulitis <https://www.nhs.uk/conditions/cellulitis/> (page checked 2024-04-18; reuse basis `ogl_v3`)
- NHS (England): Anaphylaxis <https://www.nhs.uk/conditions/anaphylaxis/> (page checked 2023-06-21; reuse basis `ogl_v3`; **source re-check overdue**)
- NHS (England): Stevens-Johnson syndrome <https://www.nhs.uk/conditions/stevens-johnson-syndrome/> (page checked 2026-03-04; reuse basis `ogl_v3`)
- NHS (England): Meningitis: symptoms <https://www.nhs.uk/conditions/meningitis/symptoms/> (page checked 2026-06-12; reuse basis `ogl_v3`)
- NHS (England): Shingles <https://www.nhs.uk/conditions/shingles/> (page checked 2023-11-23; reuse basis `ogl_v3`)
- NHS (England): Atopic eczema <https://www.nhs.uk/conditions/atopic-eczema/> (page checked 2024-09-06; reuse basis `ogl_v3`)
- NHS (England): Acne <https://www.nhs.uk/conditions/acne/> (page checked 2026-09-22; reuse basis `ogl_v3`)

**Statements to verify before approving**

- [ ] All emergency and urgent wording must match the clinic's approved urgent-symptom policy and name the local emergency number and urgent service.
- [ ] The source pages are written for the NHS in England (for example the 999 and 111 numbers and A&E); the clinic must replace them with the services available in its region.
- [ ] Two of the source pages (NHS Anaphylaxis, and others) show a scheduled review date that has passed: recheck the current wording.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

### A17. Self-care or professional care? When to see a clinician

`derm.when-to-see`, topic `when-to-see`, version 1, text hash that approval binds: `3a1c99e19467c51c…`

> Many skin concerns are reasonable to manage with gentle self-care for a few weeks. Some need a clinician's advice soon, and a few need help the same day or right away. The lists below describe which is which; if in doubt, ask.

**What it is**

- Self-care is reasonable when a concern is mild, recent, not painful, not spreading, not leaving scars, and improving or stable, with no fever.
- Booking a dermatologist appointment makes sense for concerns that last, keep coming back, leave marks or scars, or affect sleep, mood or confidence.
- Some signs need an urgent assessment, and a few call for emergency help.

**Gentle self-care**

- Keep the routine gentle and simple while the concern is mild, and keep a dated photo record.
- Decide on a review point in advance, for example about six to eight weeks for acne, or one to two weeks for an irritation after removing likely triggers.

**What to avoid**

- Waiting for weeks with a spot that is changing, bleeding or not healing.
- Trying many new products at once to 'fix' a concern.

**How to keep track**

- Look for any change in size, shape, colour, pain, itch, bleeding or spread.

**When to see a clinician**

- Self-care has not helped after about six to eight weeks (acne) or about one to two weeks (an irritation).
- Spots are deep, painful or leaving scars.
- Dryness or itching continues, or the skin is cracking or bleeding.
- A rash keeps returning, has no clear cause, or affects sleep, work or school.
- A mole or spot is new, changing, itchy, bleeding or crusting, or a sore does not heal.
- A new or unusual mark has not gone away after a few weeks, including on the palms or soles.
- A dark streak appears under a nail without an injury.
- A skin concern is affecting mood or confidence.

**Signs that need prompt help**

- Painful, hot, swollen, spreading skin, pus, red streaks, or a fever with a skin infection: ask for an urgent appointment the same day, or contact the local urgent medical helpline.
- A painful blistering rash on one side of the face or near an eye, or any eye pain, light sensitivity or blurred vision with a skin condition: urgent assessment the same day.
- Eczema with blisters, weeping, crusting or pus-filled spots and feeling unwell: urgent assessment the same day.
- Swelling of the lips, tongue, face or throat, trouble breathing, a rash that does not fade when pressed with a glass together with fever or feeling very unwell, or widespread blistering or peeling with sores in the mouth or eyes: emergency help now.

**What a photo cannot show**

- A photo cannot tell how serious something is. When a photo seems fine but the skin is painful, spreading or the person feels unwell, trust the symptoms and seek help.

**Limits the article states**

- General education, not a triage service. These lists cannot cover every situation, and anyone who feels very unwell or is worried should seek help without waiting.

**Sources**

- NHS (England): Acne <https://www.nhs.uk/conditions/acne/> (page checked 2026-09-22; reuse basis `ogl_v3`)
- American Academy of Dermatology: Acne: Tips for managing <https://www.aad.org/public/diseases/acne/skin-care/tips> (page checked 2022-11-16; reuse basis `restricted_facts_only`)
- American Academy of Dermatology: Dermatologists' top tips for relieving dry skin <https://www.aad.org/public/everyday-care/skin-care-basics/dry/dermatologists-tips-relieve-dry-skin> (page checked 2026-01-02; reuse basis `restricted_facts_only`)
- NHS (England): Atopic eczema <https://www.nhs.uk/conditions/atopic-eczema/> (page checked 2024-09-06; reuse basis `ogl_v3`)
- NHS (England): Contact dermatitis <https://www.nhs.uk/conditions/contact-dermatitis/> (page checked 2023-05-03; reuse basis `ogl_v3`; **source re-check overdue**)
- NHS (England): Rosacea <https://www.nhs.uk/conditions/rosacea/> (page checked 2023-03-17; reuse basis `ogl_v3`; **source re-check overdue**)
- NHS (England): Symptoms of melanoma skin cancer <https://www.nhs.uk/conditions/melanoma-skin-cancer/symptoms/> (page checked 2026-07-15; reuse basis `ogl_v3`)
- NHS (England): Cellulitis <https://www.nhs.uk/conditions/cellulitis/> (page checked 2024-04-18; reuse basis `ogl_v3`)
- NHS (England): Shingles <https://www.nhs.uk/conditions/shingles/> (page checked 2023-11-23; reuse basis `ogl_v3`)
- NHS (England): Meningitis: symptoms <https://www.nhs.uk/conditions/meningitis/symptoms/> (page checked 2026-06-12; reuse basis `ogl_v3`)
- NHS (England): Anaphylaxis <https://www.nhs.uk/conditions/anaphylaxis/> (page checked 2023-06-21; reuse basis `ogl_v3`; **source re-check overdue**)
- NHS (England): Stevens-Johnson syndrome <https://www.nhs.uk/conditions/stevens-johnson-syndrome/> (page checked 2026-03-04; reuse basis `ogl_v3`)

**Statements to verify before approving**

- [ ] Every urgent and emergency item, and the wording used for the urgent helpline, must match the clinic's approved urgent-symptom policy.
- [ ] Which signs need same-day assessment and which need emergency help is a clinical decision and must be confirmed by the clinic.
- [ ] The 'urgent helpline' wording is generic: the clinic should name the local number or service.

Decision: [ ] approve  [ ] approve with edits  [ ] reject    Source checked: ____________________

## Part B: warning-sign rules

Each rule reads the person's own words (a negated mention such as "no fever" does not count). A rule that fires shows the reason below and the next step. **Emergency wording is never written by the software**: it is the clinic's urgent-symptom policy message (see `REVIEW_PACKET.md`, Part B), shown exactly. A non-match must never be read as "safe".

### B: `E-airway-swelling` (emergency)

When it fires: Swelling of the lips, tongue, face, mouth or throat, or a tight throat.

What the person is told: You mentioned swelling of the lips, tongue, face, mouth or throat. Swelling like this can be a sign of a serious allergic reaction.

Article shown: `derm.warning-signs`. Sources: nhs-anaphylaxis.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `E-breathing` (emergency)

When it fires: Trouble breathing or swallowing, or wheezing, together with a skin or allergy problem.

What the person is told: You mentioned trouble breathing or swallowing with a skin or allergy problem. This can be a sign of a serious allergic reaction.

Article shown: `derm.warning-signs`. Sources: nhs-anaphylaxis.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `E-nonblanching-rash-unwell` (emergency)

When it fires: A rash that does not fade when pressed with a glass (or purple spots) together with fever, headache, stiff neck, drowsiness or feeling very unwell.

What the person is told: You described a rash that does not fade under pressure, or purple spots, together with feeling unwell. That combination can be a sign of a serious blood or brain infection.

Article shown: `derm.warning-signs`. Sources: nhs-meningitis-symptoms.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `E-blistering-mouth-eyes` (emergency)

When it fires: A spreading, painful, blistering or peeling rash with sores in the mouth, eyes, lips, throat or genitals.

What the person is told: You described blistering or peeling skin with sores in the mouth, eyes, lips, throat or genitals. That pattern can be a sign of a serious skin reaction.

Article shown: `derm.warning-signs`. Sources: nhs-stevens-johnson.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `E-infection-sepsis-signs` (emergency)

When it fires: A skin infection or a hot, swollen, painful red area together with confusion, fainting, a very fast heartbeat, shivering, clammy or pale skin or purple patches.

What the person is told: You described a skin infection or a hot, swollen, painful area together with signs that the whole body may be affected (such as confusion, fainting, a racing heart or clammy skin).

Article shown: `derm.warning-signs`. Sources: nhs-cellulitis.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `U-spreading-infection-signs` (urgent)

When it fires: Redness, swelling or a rash that is spreading quickly and is hot, painful, tender or has pus, or red streaks.

What the person is told: You described skin that is spreading quickly and is hot, painful, swollen or has pus. These can be signs of a skin infection, which is treated best when it is seen early.

Article shown: `derm.warning-signs`. Sources: nhs-cellulitis.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `U-fever-with-skin-problem` (urgent)

When it fires: A fever together with a rash, spots, sore, swelling or other skin problem.

What the person is told: You mentioned a fever together with a skin problem. A fever with a rash or an infected area is worth having looked at the same day.

Article shown: `derm.warning-signs`. Sources: nhs-cellulitis, nhs-atopic-eczema.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `U-eye-involvement` (urgent)

When it fires: Eye pain, blurred vision, light sensitivity, a red or gritty eye or swelling around the eye together with a skin condition or rash.

What the person is told: You mentioned eye symptoms (such as pain, blurred vision, light sensitivity or a red eye) together with a skin problem. Eye symptoms with a skin condition should be checked promptly.

Article shown: `derm.rosacea`. Sources: nhs-rosacea, nhs-shingles.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `U-shingles-like-rash` (urgent)

When it fires: A painful, burning or tingling blistering rash on one side of the body or face.

What the person is told: You described a painful or burning blistering rash on one side. That pattern can be shingles, which is treated best when it is seen within the first few days, and a rash near the eye needs particular care.

Article shown: `derm.warning-signs`. Sources: nhs-shingles.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `U-eczema-infected` (urgent)

When it fires: Eczema with blisters, crusting, weeping, pus, heat, swelling, pain or feeling unwell.

What the person is told: You described eczema with blistering, crusting, weeping, pus, heat or swelling. Eczema can become infected, and that needs treatment.

Article shown: `derm.eczema`. Sources: nhs-atopic-eczema.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `U-rash-after-medicine` (urgent)

When it fires: A new rash, hives, peeling or blisters that started after beginning a medicine, tablet, antibiotic or injection.

What the person is told: You described a new rash that began after starting a medicine. A rash after a new medicine should be checked the same day by the prescriber or a clinician, and the medicine should not be stopped or changed without their advice.

Article shown: `derm.warning-signs`. Sources: nhs-stevens-johnson.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `U-baby-rash-unwell` (urgent)

When it fires: A baby or infant with a rash and fever, poor feeding, drowsiness or floppiness.

What the person is told: You mentioned a baby with a rash and signs that the baby may be unwell. A baby with a rash who is feverish, not feeding well or unusually sleepy should be seen the same day.

Article shown: `derm.warning-signs`. Sources: nhs-meningitis-symptoms.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `U-severe-pain` (urgent)

When it fires: Severe or unbearable pain together with a skin problem, swelling, lump, blister or wound.

What the person is told: You described severe pain with a skin problem. Severe pain out of proportion to how the skin looks can be a sign that something deeper is wrong.

Article shown: `derm.warning-signs`. Sources: nhs-cellulitis.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `U-chemical-or-blistering-burn` (urgent)

When it fires: A chemical burn, or a sunburn that has blistered.

What the person is told: You described a chemical burn or a blistering sunburn. Burns like this should be assessed the same day.

Article shown: `derm.warning-signs`. Sources: -.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `P-changing-mole-or-spot` (prompt)

When it fires: A mole, spot or growth that is changing, getting bigger or darker, bleeding, itching, crusting, uneven, or looks different from the others.

What the person is told: You described a mole or spot that is changing or has other features that deserve a check. A change like that is a reason to have the spot examined, and most checks do not find cancer.

Article shown: `derm.moles-and-spots`. Sources: nhs-melanoma-symptoms, aad-abcdes.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `P-sore-not-healing` (prompt)

When it fires: A sore, wound or scab that does not heal, or heals and comes back.

What the person is told: You described a sore that does not heal or keeps coming back. A sore that has not healed after a few weeks should be examined.

Article shown: `derm.moles-and-spots`. Sources: nhs-melanoma-symptoms, aad-abcdes.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `P-dark-line-under-nail` (prompt)

When it fires: A dark line or streak under a fingernail or toenail.

What the person is told: You described a dark line under a nail. A dark area under a nail that was not caused by an injury should be looked at.

Article shown: `derm.moles-and-spots`. Sources: nhs-melanoma-symptoms.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `P-skin-cancer-worry` (prompt)

When it fires: A worry that a spot might be skin cancer or melanoma.

What the person is told: You mentioned a worry about skin cancer. A photo cannot tell, and an examination in person is the way to find out.

Article shown: `derm.moles-and-spots`. Sources: nhs-melanoma-symptoms.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `P-acne-needs-clinician` (prompt)

When it fires: Acne that is deep, painful, cystic or scarring, has not improved, has lasted months or years, or is affecting mood.

What the person is told: You described acne with features (such as deep or painful spots, scarring, no improvement or a long duration) for which a clinician can offer more than over-the-counter care.

Article shown: `derm.acne`. Sources: nhs-acne, aad-acne-tips.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `P-low-mood-about-skin` (prompt)

When it fires: Low mood, anxiety or embarrassment because of how the skin looks.

What the person is told: You mentioned that your skin is affecting how you feel. That matters, and it is a good reason to speak to a clinician; skin conditions can affect mental health and help is available.

Article shown: `derm.acne`. Sources: nhs-acne.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `P-raised-or-spreading-scars` (prompt)

When it fires: Raised, thick or keloid scars, or scars that are growing.

What the person is told: You described raised or growing scars. These are worth discussing with a dermatologist early, because some treatments work best before a scar matures.

Article shown: `derm.acne-marks-scars`. Sources: aad-acne-darker-skin.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `P-hair-loss` (prompt)

When it fires: Sudden, patchy or heavy hair loss, or bald patches.

What the person is told: You mentioned sudden or patchy hair loss. Hair and scalp concerns are best looked at by a clinician, and the library here does not cover hair and scalp yet.

Article shown: `-`. Sources: -.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

### B: `P-persistent-rash` (prompt)

When it fires: A rash, itch, patch, redness, bumps or dryness that the person says has lasted three weeks or more (acne has its own rule and time frame).

What the person is told: You said this has lasted for weeks. A rash or patch that has not settled after a few weeks is worth having looked at.

Article shown: `derm.when-to-see`. Sources: nhs-contact-dermatitis, nhs-atopic-eczema.

Decision: [ ] keep  [ ] change  [ ] remove    Requested change: ____________________

## Part C: how the sources are used

Every article states its sources (publisher, page, the date the page itself showed, and the reuse basis that was checked): UK Open Government Licence v3.0 pages (attribution is shown with the answer), US government public-domain pages (MedlinePlus credit is shown), and a few publishers whose terms allow only facts to be restated (those statements are written in the software's own words, are not copied, and carry no quotation). Nothing was scraped; each page was read by a person at the date listed. Please tell us if any source is not acceptable to the clinic.

## What the software tests already prove (and what it cannot)

Proven by `backend/tests/test_derm_knowledge.py`: the pack loads only when every source exists and every article has limits and claims to verify; the pack can never mark itself approved; an unreviewed article is never shown without its label (and not at all when the reference tier is off); an approved article is shown as approved only while its hash matches and its approval has not lapsed; no statement gives a diagnosis, a dose, a prescription-only drug or a certainty claim; the warning-sign rules fire on their own examples and not on their negations.

**Not proven and not provable by software:** that any sentence is clinically correct or suitable for your patients, that the rules catch everything that matters, or that keyword matching understands every way a person describes a problem. That is exactly what this review is for.
