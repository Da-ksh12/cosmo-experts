"""Patient-friendly, source-traceable explanations. Template-based on purpose: every sentence is either a fixed safe phrase or a direct statement of what the document itself says,
with its page reference and a citation to the exact line it rests on. No clinical interpretation is invented here.

Version 2 (explain-2.0.0) keeps every key and sentence of version 1 and adds:
* a citation on every statement (document, page, line text, character span, who or what read it, whether a clinician reviewed it) and a check, before the statement is shown, that the
  cited line really contains the test name, number, unit, printed range or printed flag the sentence states (ai/citations.py); a statement that fails is left out and counted;
* sections for the header facts the document prints (dates, laboratory, clinician names, explicit allergy and follow-up statements) and for the medicine entries it lists, both
  repeated as printed, with ambiguity stated and nothing recommended;
* clinic-approved background passages, with passage-level citations, when the caller supplies them (English only);
* reports of what could not be said: contradictions between documents, text that looked like instructions to software, statements dropped, and an explicit abstention when nothing
  structured could be read."""
from __future__ import annotations

from ..ai import citations, safety

VERSION = "explain-2.0.0"
LANGS = ("en", "hi", "hinglish")

T = {
    "en": {
        "processed": "We read {n} result line(s) from your document.",
        "none": "We could not confidently find individual test results in this document.",
        "says": "The report lists {name} as {value} {unit}.",
        "ref": "The report prints a reference range of {ref}.",
        "noref": "The report does not print a reference range for this, so we cannot say how it compares.",
        "printed_high": "The report itself marks this as high.",
        "printed_low": "The report itself marks this as low.",
        "outside": "This number is outside the range printed on the report.",
        "within": "This number is inside the range printed on the report.",
        "source": "Source: page {page}.",
        "uncertain": "A single value cannot tell the full story. Your doctor needs to look at it together with your history, symptoms and examination.",
        "review": "A doctor should review this report before any decisions are made.",
        "ocr": "Some text was hard to read, so please check the values against your original.",
        "q_title": "Questions you could ask your doctor",
        "questions": ["What does this result mean for me specifically?",
                      "Is any result worth repeating or following up?",
                      "Do these results change what treatment is suitable for me?"],
        "missing": "Information that may be needed: your symptoms, current medicines, and earlier reports for comparison.",
        "facts_title": "Dates and details printed on the document",
        "fact": "{label}, as printed: \"{value}\".",
        "date_ambiguous": "This date can be read in more than one way, so we have not chosen one.",
        "not_verified": "We have not checked who this is.",
        "meds_title": "Medicines listed on the document",
        "med": "The document lists: \"{raw}\".",
        "med_parts": "As printed: {parts}.",
        "med_expansion": "\"{printed}\" is commonly read as \"{plain}\". Please confirm this with your doctor or pharmacist.",
        "med_ambiguous": "Some parts of this entry are unclear or missing, so please check it with your doctor or pharmacist.",
        "med_od": "\"{printed}\" can mean \"once daily\" or \"right eye\". The document does not say which.",
        "med_slots": "A pattern such as \"{printed}\" is often read as morning, afternoon and night, but the document does not say so.",
        "med_duration": "A duration written as \"{printed}\" is not spelled out. Please confirm it with your doctor or pharmacist.",
        "med_directive": "This entry has wording about changing a medicine. It is shown exactly as printed; this summary gives no instruction about any medicine.",
        "med_corrected": "A clinician reviewed and corrected this entry.",
        "med_note": "This summary only repeats what is printed. Your doctor or pharmacist can tell you how each medicine is used.",
        "background_title": "Clinic-approved background",
        "background": "Clinic-approved information, \"{title}\": \"{text}\"",
        "background_en_only": "Clinic-approved background information is available in English only.",
        "contradiction": "Two documents print different results for {test} on {date}. This summary does not choose between them; your clinician can tell you which is right.",
        "quarantine": "Some text in this document looks like instructions to software, so it was not used in this summary.",
        "dropped": "{n} statement(s) could not be matched to the text of the document and were left out.",
        "labels": {"report_date": "Report date", "collection_date": "Sample collection date", "document_date": "Date on the document", "laboratory": "Laboratory or provider",
                   "clinician_name": "Clinician named in the document", "allergy": "Allergy statement", "follow_up": "Follow-up statement"},
        "part": {"name": "name", "strength": "strength", "form": "form", "dose": "dose", "dose_pattern": "pattern", "route": "route", "frequency": "frequency", "duration": "duration",
                 "instructions": "instructions"},
        "plain": {},
    },
    "hi": {
        "processed": "हमने आपके दस्तावेज़ से {n} परिणाम पंक्ति(याँ) पढ़ीं।",
        "none": "हम इस दस्तावेज़ में अलग-अलग जाँच परिणाम भरोसे से नहीं ढूँढ पाए।",
        "says": "रिपोर्ट में {name} का मान {value} {unit} लिखा है।",
        "ref": "रिपोर्ट में संदर्भ सीमा {ref} छपी है।",
        "noref": "रिपोर्ट में इसकी संदर्भ सीमा नहीं छपी है, इसलिए हम तुलना नहीं बता सकते।",
        "printed_high": "रिपोर्ट ने स्वयं इसे ऊँचा चिह्नित किया है।",
        "printed_low": "रिपोर्ट ने स्वयं इसे कम चिह्नित किया है।",
        "outside": "यह संख्या रिपोर्ट में छपी सीमा से बाहर है।",
        "within": "यह संख्या रिपोर्ट में छपी सीमा के अंदर है।",
        "source": "स्रोत: पृष्ठ {page}।",
        "uncertain": "केवल एक संख्या पूरी बात नहीं बताती। डॉक्टर को इसे आपके इतिहास, लक्षणों और जाँच के साथ देखना होगा।",
        "review": "कोई भी निर्णय लेने से पहले डॉक्टर को यह रिपोर्ट देखनी चाहिए।",
        "ocr": "कुछ लिखावट पढ़ना कठिन था, कृपया मान अपनी मूल रिपोर्ट से मिलाएँ।",
        "q_title": "डॉक्टर से पूछने के लिए प्रश्न",
        "questions": ["इस परिणाम का मेरे लिए विशेष रूप से क्या मतलब है?",
                      "क्या कोई परिणाम दोबारा कराने या फॉलो-अप के योग्य है?",
                      "क्या ये परिणाम मेरे लिए उपयुक्त इलाज को बदलते हैं?"],
        "missing": "ज़रूरी हो सकने वाली जानकारी: आपके लक्षण, वर्तमान दवाइयाँ, और तुलना के लिए पुरानी रिपोर्ट।",
        "facts_title": "दस्तावेज़ में छपी तारीख़ें और जानकारी",
        "fact": "{label} (जैसा छपा है): \"{value}\"।",
        "date_ambiguous": "इस तारीख़ को एक से अधिक तरीक़ों से पढ़ा जा सकता है, इसलिए हमने कोई एक नहीं चुनी।",
        "not_verified": "हमने यह जाँच नहीं की है कि यह कौन है।",
        "meds_title": "दस्तावेज़ में लिखी दवाइयाँ",
        "med": "दस्तावेज़ में यह लिखा है: \"{raw}\"।",
        "med_parts": "जैसा छपा है: {parts}।",
        "med_expansion": "\"{printed}\" को आम तौर पर \"{plain}\" पढ़ा जाता है। कृपया अपने डॉक्टर या फार्मासिस्ट से इसकी पुष्टि करें।",
        "med_ambiguous": "इस पंक्ति के कुछ हिस्से अस्पष्ट हैं या छूटे हुए हैं, इसलिए कृपया अपने डॉक्टर या फार्मासिस्ट से जाँच लें।",
        "med_od": "\"{printed}\" का मतलब \"दिन में एक बार\" या \"दाहिनी आँख\" हो सकता है। दस्तावेज़ यह नहीं बताता कि कौन सा।",
        "med_slots": "\"{printed}\" जैसा पैटर्न अक्सर सुबह, दोपहर और रात के रूप में पढ़ा जाता है, लेकिन दस्तावेज़ में यह नहीं लिखा है।",
        "med_duration": "\"{printed}\" के रूप में लिखी अवधि स्पष्ट नहीं है। कृपया अपने डॉक्टर या फार्मासिस्ट से इसकी पुष्टि करें।",
        "med_directive": "इस पंक्ति में दवा बदलने से जुड़े शब्द हैं। वे ठीक वैसे ही दिखाए गए हैं जैसे छपे हैं; यह सारांश किसी दवा के बारे में कोई निर्देश नहीं देता।",
        "med_corrected": "एक चिकित्सक ने इस पंक्ति को देखकर सुधारा है।",
        "med_note": "यह सारांश केवल वही दोहराता है जो छपा है। हर दवा कैसे ली जाती है, यह आपके डॉक्टर या फार्मासिस्ट बता सकते हैं।",
        "background_title": "क्लिनिक द्वारा स्वीकृत पृष्ठभूमि",
        "background": "क्लिनिक द्वारा स्वीकृत जानकारी, \"{title}\": \"{text}\"",
        "background_en_only": "क्लिनिक द्वारा स्वीकृत पृष्ठभूमि जानकारी केवल अंग्रेज़ी में उपलब्ध है।",
        "contradiction": "दो दस्तावेज़ों में {date} को {test} के अलग-अलग परिणाम छपे हैं। यह सारांश उनमें से किसी को नहीं चुनता; आपके चिकित्सक बता सकते हैं कि कौन सही है।",
        "quarantine": "इस दस्तावेज़ के कुछ शब्द सॉफ़्टवेयर को दिए गए निर्देश जैसे लगते हैं, इसलिए उन्हें इस सारांश में इस्तेमाल नहीं किया गया।",
        "dropped": "{n} कथन दस्तावेज़ के पाठ से मिलाए नहीं जा सके और छोड़ दिए गए।",
        "labels": {"report_date": "रिपोर्ट की तारीख़", "collection_date": "नमूना लेने की तारीख़", "document_date": "दस्तावेज़ पर तारीख़", "laboratory": "प्रयोगशाला या प्रदाता",
                   "clinician_name": "दस्तावेज़ में नामित चिकित्सक", "allergy": "एलर्जी का कथन", "follow_up": "फॉलो-अप का कथन"},
        "part": {"name": "नाम", "strength": "मात्रा", "form": "रूप", "dose": "खुराक", "dose_pattern": "पैटर्न", "route": "मार्ग", "frequency": "आवृत्ति", "duration": "अवधि", "instructions": "निर्देश"},
        "plain": {"once daily": "दिन में एक बार", "twice daily": "दिन में दो बार", "three times daily": "दिन में तीन बार", "four times daily": "दिन में चार बार", "at bedtime": "सोने के समय",
                  "as needed": "ज़रूरत पड़ने पर", "every other day": "एक दिन छोड़कर"},
    },
    "hinglish": {
        "processed": "Humne aapke document se {n} result line(s) padhi.",
        "none": "Hum is document me alag-alag test results bharose ke saath nahi dhoondh paaye.",
        "says": "Report me {name} ki value {value} {unit} likhi hai.",
        "ref": "Report me reference range {ref} chhapi hai.",
        "noref": "Report me iski reference range nahi chhapi, isliye hum compare nahi bata sakte.",
        "printed_high": "Report ne khud ise high mark kiya hai.",
        "printed_low": "Report ne khud ise low mark kiya hai.",
        "outside": "Ye number report me chhapi range ke bahar hai.",
        "within": "Ye number report me chhapi range ke andar hai.",
        "source": "Source: page {page}.",
        "uncertain": "Sirf ek number poori kahani nahi batata. Doctor ko ise aapki history, symptoms aur examination ke saath dekhna hoga.",
        "review": "Koi bhi decision lene se pehle doctor ko ye report dekhni chahiye.",
        "ocr": "Kuch text padhna mushkil tha, please values apni original report se match kar lein.",
        "q_title": "Doctor se poochne ke sawaal",
        "questions": ["Is result ka mere liye specifically kya matlab hai?",
                      "Kya koi result dobara karwana ya follow-up karna chahiye?",
                      "Kya in results se mere liye suitable treatment badalta hai?"],
        "missing": "Zaroori ho sakne wali information: aapke symptoms, current medicines, aur comparison ke liye purani reports.",
        "facts_title": "Document me chhapi dates aur details",
        "fact": "{label}, jaisa chhapa hai: \"{value}\".",
        "date_ambiguous": "Is date ko ek se zyada tareeke se padha ja sakta hai, isliye humne koi ek nahi chuni.",
        "not_verified": "Humne check nahi kiya hai ki ye kaun hai.",
        "meds_title": "Document me likhi medicines",
        "med": "Document me ye likha hai: \"{raw}\".",
        "med_parts": "Jaisa chhapa hai: {parts}.",
        "med_expansion": "\"{printed}\" ko aam taur par \"{plain}\" padha jata hai. Please apne doctor ya pharmacist se confirm karein.",
        "med_ambiguous": "Is entry ke kuch hisse spasht nahi hain ya chhoote hue hain, isliye please apne doctor ya pharmacist se check karein.",
        "med_od": "\"{printed}\" ka matlab \"din me ek baar\" ya \"right eye\" ho sakta hai. Document ye nahi batata ki kaun sa.",
        "med_slots": "\"{printed}\" jaisa pattern aksar subah, dopahar aur raat ke roop me padha jata hai, lekin document me ye likha nahi hai.",
        "med_duration": "\"{printed}\" ke roop me likhi duration spasht nahi hai. Please apne doctor ya pharmacist se confirm karein.",
        "med_directive": "Is entry me medicine badalne se jude shabd hain. Wo bilkul waise hi dikhaye gaye hain jaise chhapa hai; ye summary kisi medicine ke baare me koi instruction nahi deta.",
        "med_corrected": "Ek clinician ne is entry ko dekhkar sahi kiya hai.",
        "med_note": "Ye summary sirf wahi dohrati hai jo chhapa hai. Har medicine kaise li jaati hai, ye aapke doctor ya pharmacist bata sakte hain.",
        "background_title": "Clinic dwara approved background",
        "background": "Clinic dwara approved information, \"{title}\": \"{text}\"",
        "background_en_only": "Clinic dwara approved background information sirf English me available hai.",
        "contradiction": "Do documents me {date} ko {test} ke alag-alag results chhape hain. Ye summary unme se kisi ko nahi chunti; aapke clinician bata sakte hain ki kaun sahi hai.",
        "quarantine": "Is document ke kuch shabd software ko diye gaye instruction jaise lagte hain, isliye unhe is summary me use nahi kiya gaya.",
        "dropped": "{n} statement document ke text se match nahi ho paaye aur chhod diye gaye.",
        "labels": {"report_date": "Report ki date", "collection_date": "Sample lene ki date", "document_date": "Document par date", "laboratory": "Laboratory ya provider",
                   "clinician_name": "Document me bataye gaye clinician", "allergy": "Allergy ka statement", "follow_up": "Follow-up ka statement"},
        "part": {"name": "naam", "strength": "strength", "form": "form", "dose": "dose", "dose_pattern": "pattern", "route": "route", "frequency": "frequency", "duration": "duration",
                 "instructions": "instructions"},
        "plain": {"once daily": "din me ek baar", "twice daily": "din me do baar", "three times daily": "din me teen baar", "four times daily": "din me chaar baar",
                  "at bedtime": "sote waqt", "as needed": "zaroorat padne par", "every other day": "ek din chhodkar"},
    },
}

SPEAK_LANG = {"en": "en-IN", "hi": "hi-IN", "hinglish": "en-IN"}
PART_ORDER = ("name", "strength", "form", "dose", "dose_pattern", "route", "frequency", "duration", "instructions")


def _med_parts(entry: dict, t: dict) -> str:
    def raw(c):
        return (c or {}).get("raw", "") if isinstance(c, dict) else ""
    vals = {"name": raw(entry.get("name")), "strength": ", ".join(raw(s) for s in entry.get("strengths") or [] if raw(s)), "form": raw(entry.get("form")), "dose": raw(entry.get("dose")),
            "dose_pattern": raw(entry.get("dose_pattern")), "route": raw(entry.get("route")), "frequency": raw(entry.get("frequency")), "duration": raw(entry.get("duration")),
            "instructions": "; ".join(raw(i) for i in entry.get("instructions") or [] if raw(i))}
    return "; ".join(f"{t['part'][k]} \"{vals[k]}\"" for k in PART_ORDER if vals[k])


SPECIFIC_AMBIGUITY = {"od_could_mean_right_eye_or_once_daily": ("med_od", "frequency"), "slot_pattern_convention_assumed": ("med_slots", "dose_pattern"),
                      "ambiguous_duration_shorthand": ("med_duration", "duration")}


def _expansions(entry: dict, t: dict, amb: list) -> list[str]:
    """Shorthand the parser expanded from its fixed list, restated as 'commonly read as' with a request to confirm. Only the frequency lexicon is restated; nothing else is explained.
    Where the parser found that the shorthand has a second meaning in this context (OD beside an eye medicine), only the doubt is stated: a one-sided expansion would mislead."""
    out = []
    f = entry.get("frequency") or {}
    plain = (f.get("normalized") or {}).get("plain") if isinstance(f.get("normalized"), dict) else None
    if plain and "abbreviation expanded" in (f.get("note") or "") and "od_could_mean_right_eye_or_once_daily" not in amb:
        out.append(t["med_expansion"].format(printed=f.get("raw", ""), plain=t["plain"].get(plain, plain)))
    for code, (key, comp) in SPECIFIC_AMBIGUITY.items():
        if code in amb and isinstance(entry.get(comp), dict) and entry[comp].get("raw"):
            out.append(t[key].format(printed=entry[comp]["raw"]))
    return out


def explain_document(document: dict, fields: list[dict], pages: list[dict], lang: str = "en", *, facts: list[dict] | None = None, medications: list[dict] | None = None,
                     background: list[dict] | None = None, contradictions: list[dict] | None = None, quarantined: int = 0, page_texts: dict[int, str] | None = None) -> dict:
    lang = lang if lang in T else "en"
    t = T[lang]
    sections = []
    items = []
    dropped: list[dict] = []
    evidence = [f["source_text"] for f in fields] + [fa["source_text"] for fa in facts or []] + [m["raw"] for m in medications or []] + [h["text"] for h in background or [] if h.get("text")]

    def screened(text: str, item: str, id_) -> bool:
        """Every statement passes the shared safety policy before it is shown. What the document itself prints is repeated inside quotation marks, which the policy treats as a quotation
        of the source (labelled, not as the system speaking); anything else that reads as a diagnosis or a medicine change is dropped and counted."""
        rev = safety.review_text(text, kind="extraction", evidence=evidence)
        if not rev.allowed:
            dropped.append({"item": item, "id": id_, "statement": "text", "reason": "safety_policy:" + ",".join(sorted(set(rev.codes())))})
        return rev.allowed

    def keep(kind: str, f: dict) -> bool:
        why = citations.field_statement_problem(kind, f)
        if why:
            dropped.append({"item": "field", "id": f.get("id"), "statement": kind, "reason": why})
        return not why

    for f in fields:
        if not keep("value", f):
            continue                                   # the sentence about the value cannot be supported by its cited line: say nothing about this field
        lines = [t["says"].format(name=f["name"], value=f["value"], unit=f["unit"] or "").replace("  ", " ")]
        if not f["ref_range"]:
            lines.append(t["noref"])
        elif keep("range", f):
            lines.append(t["ref"].format(ref=f["ref_range"]))
        flag = {"printed_flag_high": ("printed_high", "printed_flag_high"), "printed_flag_low": ("printed_low", "printed_flag_low"),
                "outside_printed_range": ("outside", "outside"), "within_printed_range": ("within", "within")}.get(f["flag"])
        if flag and keep(flag[1], f):
            lines.append(t[flag[0]])
        lines.append(t["source"].format(page=f["source_page"]))
        cite = citations.document_citation(document["id"], page=f["source_page"], excerpt=f["source_text"], item="field", item_id=f.get("id"),
                                           span=citations.as_dict(f.get("provenance")).get("line_span"), version=f.get("processing_version"), review_status=f.get("review_status"))
        if not screened(" ".join(lines), "field", f.get("id")):
            continue
        items.append({"field_id": f["id"], "text": " ".join(l for l in lines if l),
                      "source": {"document_id": document["id"], "page": f["source_page"], "text": f["source_text"],
                                 "processing_version": f["processing_version"]},
                      "citation": cite, "review_status": f["review_status"]})
    sections.append({"title": "What we found", "summary": t["processed"].format(n=len(items)) if items else t["none"], "items": items})

    # ---- header facts as printed
    fact_items = []
    for fa in facts or []:
        if fa.get("review_status") == "rejected":
            continue
        why = citations.fact_statement_problem(fa)
        if why:
            dropped.append({"item": "fact", "id": fa.get("id"), "statement": fa.get("kind"), "reason": why})
            continue
        amb = citations.as_list(fa.get("ambiguity"))
        lines = [t["fact"].format(label=t["labels"].get(fa["kind"], fa.get("label") or fa["kind"]), value=fa["value"])]
        if {"day_month_order_ambiguous", "two_digit_year"} & set(amb):
            lines.append(t["date_ambiguous"])
        if "credentials_not_verified" in amb:
            lines.append(t["not_verified"])
        lines.append(t["source"].format(page=fa["source_page"]))
        prov = citations.as_dict(fa.get("provenance"))
        if not screened(" ".join(lines), "fact", fa.get("id")):
            continue
        fact_items.append({"fact_id": fa.get("id"), "kind": fa["kind"], "text": " ".join(lines), "ambiguity": amb, "review_status": fa.get("review_status"),
                           "citation": citations.document_citation(document["id"], page=fa["source_page"], excerpt=fa["source_text"], item="fact", item_id=fa.get("id"), span=prov.get("span"),
                                                                   version=prov.get("extractor"), review_status=fa.get("review_status"))})
    if fact_items:
        sections.append({"title": t["facts_title"], "summary": "", "items": fact_items})

    # ---- medicine entries as printed
    med_items = []
    for md in medications or []:
        if md.get("review_status") == "rejected":
            continue
        entry = md.get("entry") or {}
        if (why := citations.medication_statement_problem(md, (page_texts or {}).get(md.get("page")))):
            dropped.append({"item": "medication", "id": md.get("id"), "statement": "entry", "reason": why})
            continue
        lines = [t["med"].format(raw=md["raw"])]
        parts = _med_parts(entry, t)
        if parts:
            lines.append(t["med_parts"].format(parts=parts))
        amb = citations.as_list(md.get("ambiguity")) or entry.get("ambiguity") or []
        lines += _expansions(entry, t, amb)
        if "contains_change_directive_as_printed" in amb:
            lines.append(t["med_directive"])
        elif any(not str(a).startswith("abbreviation_expanded") and a not in SPECIFIC_AMBIGUITY for a in amb):
            lines.append(t["med_ambiguous"])
        if md.get("review_status") == "corrected":
            lines.append(t["med_corrected"])
        lines.append(t["source"].format(page=md.get("page")))
        if not screened(" ".join(lines), "medication", md.get("id")):
            continue
        med_items.append({"medication_id": md.get("id"), "text": " ".join(lines), "ambiguity": amb, "review_status": md.get("review_status"),
                          "citation": citations.document_citation(document["id"], page=md.get("page"), excerpt=md["raw"], item="medication", item_id=md.get("id"),
                                                                  span=entry.get("span"), version=md.get("parser"), review_status=md.get("review_status"))})
    if med_items:
        sections.append({"title": t["meds_title"], "summary": t["med_note"], "items": med_items})

    # ---- approved background (English articles only)
    bg_items = []
    if background is not None:
        if lang != "en":
            sections.append({"title": t["background_title"], "summary": t["background_en_only"], "items": []})
        else:
            for h in background:
                txt = t["background"].format(title=h["title"], text=h["text"])
                if screened(txt, "background", h.get("passage_id")):
                    bg_items.append({"text": txt, "citation": {k: v for k, v in h.items() if k != "text"}})
            if bg_items:
                sections.append({"title": t["background_title"], "summary": "", "items": bg_items})

    uncertain = [t["uncertain"], t["missing"]]
    if any(p["method"] == "ocr" and (p["ocr_confidence"] or 0) < 0.6 for p in pages):
        uncertain.append(t["ocr"])
    for c in contradictions or []:
        uncertain.append(t["contradiction"].format(test=c["test"], date=c["date"]))
    if quarantined:
        uncertain.append(t["quarantine"])
    if dropped:
        uncertain.append(t["dropped"].format(n=len(dropped)))
    sections.append({"title": "What is still uncertain", "summary": " ".join(uncertain), "items": []})
    sections.append({"title": t["q_title"], "summary": "", "items": [{"text": q} for q in t["questions"]]})
    spoken = " ".join([sections[0]["summary"]] + [i["text"] for i in items[:10]] + [t["uncertain"], t["review"]])
    readable = bool(items or fact_items or med_items)
    out = {
        "document_id": document["id"], "language": lang, "speech_language": SPEAK_LANG[lang], "version": VERSION,
        "status": "clinician_review_required", "clinician_review_message": t["review"],
        "sections": sections, "spoken_text": spoken,
        "disclaimer": "This is a plain-language summary of what your document says. It is not a diagnosis.",
        "translation_reviewed": lang == "en",
        "verification": {"statements_dropped": len(dropped), "dropped": dropped},
        "contradictions": contradictions or [], "quarantined_text": quarantined,
    }
    if not readable:
        out["abstained"] = True
        out["abstention"] = {"reason": "insufficient_evidence", "message": t["none"]}
    return out
