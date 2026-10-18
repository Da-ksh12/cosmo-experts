"""Synthetic prescription lines with gold annotations, for the medication parser.

SYNTHETIC FIXTURES: written by the project to exercise the rules, from how a person reads each line (not from the parser's output). They show that the code does what it says on
these formats. They are NOT real prescriptions and prove nothing about accuracy on real documents; real-world evaluation needs consented, de-identified prescriptions that this
repository does not have.

Gold keys: name, form, strengths (list of raw strings), dose, pattern, frequency, duration, route, instructions (list; matched by overlap), alias, ambiguity (codes that MUST be flagged),
absent (components that MUST stay empty: the parser must not invent them). Raw strings are exactly as printed."""

PRESCRIPTION_LINES = [
    # ---- Indian-style shorthand
    dict(text="1. Tab. Augmentin 625 mg 1-0-1 x 5 days after food", name="Augmentin", form="Tab", strengths=["625 mg"], pattern="1-0-1", duration="x 5 days", instructions=["after food"],
         ambiguity=["slot_pattern_convention_assumed"], absent=["route", "dose"]),
    dict(text="Tab Pantoprazole 40mg OD before breakfast x 14 days", name="Pantoprazole", form="Tab", strengths=["40mg"], frequency="OD", duration="x 14 days", instructions=["before breakfast"],
         ambiguity=["abbreviation_expanded:frequency"], absent=["route", "pattern"]),
    dict(text="Cap. Doxycycline 100 mg BD x 7/7", name="Doxycycline", form="Cap", strengths=["100 mg"], frequency="BD", duration="x 7/7", ambiguity=["ambiguous_duration_shorthand"], absent=["route"]),
    dict(text="Syp. Cetirizine 5 ml HS x 5 days", name="Cetirizine", form="Syp", dose="5 ml", frequency="HS", duration="x 5 days", absent=["strengths", "route", "pattern"]),
    dict(text="Cream Mupirocin 2% apply thin layer on affected area twice daily x 7 days", name="Mupirocin", form="Cream", strengths=["2%"], frequency="twice daily", duration="x 7 days",
         instructions=["apply thin layer on affected area twice daily x 7 days"], absent=["route", "pattern"]),
    dict(text="Tab. Paracetamol 500mg SOS", name="Paracetamol", form="Tab", strengths=["500mg"], frequency="SOS", ambiguity=["as_needed_no_fixed_frequency"], absent=["duration", "route"]),
    dict(text="Tab. Fexofenadine 120 mg OD at night x 10 days", name="Fexofenadine", form="Tab", strengths=["120 mg"], frequency="OD", duration="x 10 days", absent=["route"]),
    dict(text="Eye drops Lubrex 1 drop OD both eyes", name="Lubrex", form="drops", dose="1 drop", frequency="OD", route="Eye", ambiguity=["od_could_mean_right_eye_or_once_daily"], absent=["duration"]),
    dict(text="Tab. Amoxicillin 500 mg + Clavulanic acid 125 mg 1-0-1 x 5 days", name="Amoxicillin", form="Tab", strengths=["500 mg", "125 mg"], pattern="1-0-1", duration="x 5 days",
         ambiguity=["combination_or_multiple_strengths", "slot_pattern_convention_assumed"], absent=["route"]),
    dict(text="Tab. Augmentin (amoxicillin + clavulanic acid) 625 mg BD x 5 days", name="Augmentin", form="Tab", alias="amoxicillin + clavulanic acid", strengths=["625 mg"], frequency="BD",
         duration="x 5 days", absent=["route"]),
    dict(text="Tab. Vitamin D3 60000 IU weekly x 8 weeks", name="Vitamin D3", form="Tab", strengths=["60000 IU"], frequency="weekly", duration="x 8 weeks", absent=["route"]),
    dict(text="Tab. Folvite 5 OD", name="Folvite", form="Tab", strengths=["5"], frequency="OD", ambiguity=["strength_unit_missing"], absent=["duration", "route"]),
    dict(text="Tab. Cefixime 2OO mg BD x 5 days", name="Cefixime", form="Tab", strengths=["2OO mg"], frequency="BD", duration="x 5 days", ambiguity=["possible_letter_digit_confusion"], absent=["route"]),
    dict(text="Tab. Prednisolone 10 mg OD taper over 2 weeks, stop after review", name="Prednisolone", form="Tab", strengths=["10 mg"], frequency="OD",
         instructions=["stop after review"], ambiguity=["contains_change_directive_as_printed"], absent=["route"]),
    dict(text="Tab. Levocetirizine 5 mg OD\n    at night for 10 days", name="Levocetirizine", form="Tab", strengths=["5 mg"], frequency="OD", duration="for 10 days", absent=["route"]),
    dict(text="Tab. Hydroxychloroquine 200mg BD after meals", name="Hydroxychloroquine", form="Tab", strengths=["200mg"], frequency="BD", instructions=["after meals"], absent=["route", "duration"]),
    dict(text="Lotion Minoxidil 5% apply 1 ml to scalp twice daily", name="Minoxidil", form="Lotion", strengths=["5%"], dose="1 ml", frequency="twice daily", instructions=["apply 1 ml to scalp twice daily"], absent=["duration"]),
    dict(text="Shampoo Ketoconazole 2% twice weekly leave on 5 minutes", name="Ketoconazole", form="Shampoo", strengths=["2%"], frequency="twice weekly", instructions=["leave on 5 minutes"], absent=["duration", "route"]),
    dict(text="2. Tab. Isotroin 20 mg once daily with food", name="Isotroin", form="Tab", strengths=["20 mg"], frequency="once daily", instructions=["with food"], absent=["duration", "route"]),
    dict(text="Inj. Triamcinolone 10 mg/ml 0.1 ml intralesional monthly", name="Triamcinolone", form="Inj", strengths=["10 mg/ml"], dose="0.1 ml", route="intralesional", frequency="monthly",
         ambiguity=["abbreviation_expanded:form"], absent=["duration"]),
    dict(text="Tab. Itraconazole 100 mg BD for 1 week after food", name="Itraconazole", form="Tab", strengths=["100 mg"], frequency="BD", duration="for 1 week", instructions=["after food"], absent=["route"]),
    dict(text="Gel Adapalene 0.1% at night", name="Adapalene", form="Gel", strengths=["0.1%"], frequency="at night", absent=["duration", "route"]),
    dict(text="Tab. Metformin 500 mg 1-0-1 continue", name="Metformin", form="Tab", strengths=["500 mg"], pattern="1-0-1", duration="continue", ambiguity=["open_ended_duration"], absent=["route"]),
    dict(text="Tab. Zinc 50 mg OD x 3 months", name="Zinc", form="Tab", strengths=["50 mg"], frequency="OD", duration="x 3 months", absent=["route"]),
    dict(text="Syp. Ibugesic 100mg/5ml 5 ml TDS x 3 days", name="Ibugesic", form="Syp", strengths=["100mg/5ml"], dose="5 ml", frequency="TDS", duration="x 3 days", absent=["route"]),
    # ---- US-style and prose
    dict(text="Rx: Amoxicillin 500 mg capsule - take 1 capsule by mouth three times daily for 7 days", name="Amoxicillin", form="capsule", strengths=["500 mg"], dose="1 capsule",
         frequency="three times daily", duration="for 7 days", route="by mouth", absent=["pattern"]),
    dict(text="Doxycycline 100 mg tablet, 1 tablet twice daily for 10 days with water", name="Doxycycline", form="tablet", strengths=["100 mg"], dose="1 tablet", frequency="twice daily",
         duration="for 10 days", instructions=["with water"], absent=["route"]),
    dict(text="Tab. Methotrexate 7.5 mg once weekly", name="Methotrexate", form="Tab", strengths=["7.5 mg"], frequency="once weekly", absent=["duration", "route"]),
    dict(text="Prednisone 5 mg q8h for 5 days", name="Prednisone", strengths=["5 mg"], frequency="q8h", duration="for 5 days", absent=["route", "form"]),
    dict(text="Tab. Betahistine 16 mg", name="Betahistine", form="Tab", strengths=["16 mg"], ambiguity=["frequency_missing"], absent=["frequency", "duration", "route", "pattern"]),
    dict(text="Tab. Levothyroxine 50 mcg OD empty stomach in the morning", name="Levothyroxine", form="Tab", strengths=["50 mcg"], frequency="OD", instructions=["empty stomach"], absent=["duration", "route"]),
    dict(text="Tab. Azithromycin 500mg once daily x 3 days avoid antacids", name="Azithromycin", form="Tab", strengths=["500mg"], frequency="once daily", duration="x 3 days",
         instructions=["avoid antacids"], absent=["route"]),
    dict(text="Cap. Omeprazole 20 mg BD ac", name="Omeprazole", form="Cap", strengths=["20 mg"], frequency="BD", instructions=["ac"], ambiguity=["abbreviation_expanded:instruction"], absent=["duration"]),
    dict(text="Tab. Metoprolol 25 mg BD STT", name="Metoprolol", form="Tab", strengths=["25 mg"], frequency="BD", ambiguity=["unrecognised_abbreviation:STT"], absent=["duration", "route"]),
]

# Lines that look similar but are NOT medicine entries: the parser must return nothing for them.
NOT_MEDICATION_LINES = [
    "Haemoglobin 11.2 g/dL 12.0 - 15.5 L",
    "Ferritin 8 ng/mL 15 - 150",
    "Date: 12/03/2024  Dr. A. Kumar  Reg. No. 12345",
    "Patient: Asha Rao  Age 34  F",
    "Follow up after 2 weeks",
    "Blood pressure 120/80 mmHg",
    "Total Cholesterol 190 mg/dL < 200",
    "TSH 2.1 mIU/L 0.4 - 4.0",
]

# Multi-line documents: (document text, doc_type, expected number of medicine entries)
DOCUMENTS = [
    ("CITY SKIN CLINIC\nDr. A. Kumar MD\nDate: 12/03/2024\nRx\n1. Tab. Doxycycline 100 mg BD x 7 days after food\n2. Cream Mupirocin 2% apply twice daily x 5 days\n3. Tab. Cetirizine 10 mg OD at night\nFollow up after 2 weeks\nAllergies: NKDA",
     "prescription", 3),
    ("DIAGNOSTIC LABS\nReport Date: 12/03/2024\nHaemoglobin 11.2 g/dL 12.0 - 15.5 L\nFerritin 8 ng/mL 15 - 150 L\nTSH 2.1 mIU/L 0.4 - 4.0\n", "laboratory_report", 0),
]


# ---------------------------------------------------------------------------------------------------------------------------------------------------------------------
# HELD-OUT set: written AFTER the parser had been tuned on PRESCRIPTION_LINES, from how a person reads each line, and run against the parser exactly once before any further
# change (the first-run figures are recorded in docs/eval/medication_parser.json). It includes formats the parser is NOT expected to handle ("T." for tablet, "right eye",
# "as needed"): they are in here on purpose so the report shows the limits. Once parser changes were made in response, this set is no longer independent evidence for those fixes.
HELDOUT_LINES = [
    dict(text="1) Tab Telmisartan 40 mg 1-0-0 for 30 days", name="Telmisartan", form="Tab", strengths=["40 mg"], pattern="1-0-0", duration="for 30 days", absent=["route", "frequency"]),
    dict(text="Cap. Fluconazole 150 mg once a week x 4 weeks", name="Fluconazole", form="Cap", strengths=["150 mg"], frequency="once a week", duration="x 4 weeks", absent=["route"]),
    dict(text="Oint. Betamethasone 0.05% apply on affected area BD for 1 week", name="Betamethasone", form="Oint", strengths=["0.05%"], frequency="BD", duration="for 1 week",
         instructions=["apply on affected area BD for 1 week"], absent=["route"]),
    dict(text="Syp. Amoxicillin 125 mg/5 ml 5 ml TID x 5 days", name="Amoxicillin", form="Syp", strengths=["125 mg/5 ml"], dose="5 ml", frequency="TID", duration="x 5 days", absent=["route"]),
    dict(text="Tab. Rabeprazole 20 mg OD empty stomach", name="Rabeprazole", form="Tab", strengths=["20 mg"], frequency="OD", instructions=["empty stomach"], absent=["duration", "route"]),
    dict(text="Inj. Vitamin B12 1000 mcg IM weekly x 4 weeks", name="Vitamin B12", form="Inj", strengths=["1000 mcg"], route="IM", frequency="weekly", duration="x 4 weeks"),
    dict(text="Tab. Ondansetron 4mg SOS", name="Ondansetron", form="Tab", strengths=["4mg"], frequency="SOS", absent=["duration", "route"]),
    dict(text="Tab. Atorvastatin 10 mg HS", name="Atorvastatin", form="Tab", strengths=["10 mg"], frequency="HS", absent=["duration", "route"]),
    dict(text="Gel Clindamycin 1% + Nicotinamide 4% apply at night", name="Clindamycin", form="Gel", strengths=["1%", "4%"], frequency="at night", ambiguity=["combination_or_multiple_strengths"],
         instructions=["apply at night"], absent=["duration", "route"]),
    dict(text="Nasal spray Fluticasone 2 puffs each nostril OD", name="Fluticasone", form="spray", dose="2 puffs", frequency="OD", route="Nasal", absent=["strengths", "duration"]),
    dict(text="Tab. Calcium + Vitamin D3 500 mg 1-0-1 after meals", name="Calcium + Vitamin D3", form="Tab", strengths=["500 mg"], pattern="1-0-1", instructions=["after meals"], absent=["route"]),
    dict(text="Cap. Vitamin E 400 IU OD x 2 months", name="Vitamin E", form="Cap", strengths=["400 IU"], frequency="OD", duration="x 2 months", absent=["route"]),
    dict(text="Tab. Dolo 650 SOS for fever", name="Dolo", form="Tab", strengths=["650"], frequency="SOS", ambiguity=["strength_unit_missing"], absent=["route", "duration"]),
    dict(text="Lotion Calamine apply as needed", name="Calamine", form="Lotion", frequency="as needed", instructions=["apply as needed"], absent=["strengths", "duration", "route"]),
    dict(text="Tab. Cetirizine 10mg 0-0-1 x 7/7", name="Cetirizine", form="Tab", strengths=["10mg"], pattern="0-0-1", duration="x 7/7", ambiguity=["ambiguous_duration_shorthand"], absent=["route"]),
    dict(text="Tab. Sertraline 50 mg OD, stop if dizzy", name="Sertraline", form="Tab", strengths=["50 mg"], frequency="OD", instructions=["stop if dizzy"],
         ambiguity=["contains_change_directive_as_printed"], absent=["route", "duration"]),
    dict(text="T. Zincovit 1 tab OD after lunch", name="Zincovit", form="T", dose="1 tab", frequency="OD", instructions=["after lunch"], absent=["strengths", "duration", "route"]),
    dict(text="Cap Pregabalin 75mg OD at bedtime", name="Pregabalin", form="Cap", strengths=["75mg"], frequency="OD", absent=["duration", "route"]),
    dict(text="Drops Tobramycin 0.3% 1 drop QID right eye", name="Tobramycin", form="Drops", strengths=["0.3%"], dose="1 drop", frequency="QID", route="right eye", absent=["duration"]),
    dict(text="Tab. Lisinopril 10 mg once daily x 90 days. Do not stop abruptly", name="Lisinopril", form="Tab", strengths=["10 mg"], frequency="once daily", duration="x 90 days",
         instructions=["Do not stop abruptly"], absent=["route"]),
    dict(text="Tab. Ibuprofen 400 mg TDS with food x 5 days", name="Ibuprofen", form="Tab", strengths=["400 mg"], frequency="TDS", duration="x 5 days", instructions=["with food"], absent=["route"]),
    dict(text="Tab. Paracetamol 650 mg q6h prn", name="Paracetamol", form="Tab", strengths=["650 mg"], frequency="q6h prn", absent=["duration", "route"]),
    dict(text="Cream Hydrocortisone 1% apply thinly BD x 7 days", name="Hydrocortisone", form="Cream", strengths=["1%"], frequency="BD", duration="x 7 days", instructions=["apply thinly BD x 7 days"], absent=["route"]),
    dict(text="Tab. Folic acid 5mg OD", name="Folic acid", form="Tab", strengths=["5mg"], frequency="OD", absent=["duration", "route"]),
    dict(text="Syp Lactulose 15 ml HS", name="Lactulose", form="Syp", dose="15 ml", frequency="HS", absent=["strengths", "duration", "route"]),
    dict(text="Cap Esomeprazole 40 mg OD before breakfast x 8 weeks", name="Esomeprazole", form="Cap", strengths=["40 mg"], frequency="OD", duration="x 8 weeks", instructions=["before breakfast"], absent=["route"]),
]
HELDOUT_NOT_MEDICATION = [
    "Weight 62 kg  Height 160 cm", "BP 130/85 mmHg", "Hb 11.2 g/dL", "Next visit: 12/04/2024", "Allergy: sulfa drugs", "Advice: avoid sun exposure and use sunscreen SPF 30",
    "He was advised to take tablets regularly", "Creatinine 0.9 mg/dL 0.6 - 1.2", "Vitamin D 18 ng/mL 30 - 100 L",
]
