"""Patient-friendly, source-traceable explanations. Template-based on purpose: every sentence is either a
fixed safe phrase or a direct statement of what the document itself says, with its page reference.
No clinical interpretation is invented here."""
from __future__ import annotations

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
    },
}

SPEAK_LANG = {"en": "en-IN", "hi": "hi-IN", "hinglish": "en-IN"}


def explain_document(document: dict, fields: list[dict], pages: list[dict], lang: str = "en") -> dict:
    lang = lang if lang in T else "en"
    t = T[lang]
    sections = []
    items = []
    for f in fields:
        lines = [t["says"].format(name=f["name"], value=f["value"], unit=f["unit"]).replace("  ", " ")]
        lines.append(t["ref"].format(ref=f["ref_range"]) if f["ref_range"] else t["noref"])
        lines.append({"printed_flag_high": t["printed_high"], "printed_flag_low": t["printed_low"],
                      "outside_printed_range": t["outside"], "within_printed_range": t["within"]}.get(f["flag"], ""))
        lines.append(t["source"].format(page=f["source_page"]))
        items.append({"field_id": f["id"], "text": " ".join(l for l in lines if l),
                      "source": {"document_id": document["id"], "page": f["source_page"], "text": f["source_text"],
                                 "processing_version": f["processing_version"]},
                      "review_status": f["review_status"]})
    sections.append({"title": "What we found", "summary": t["processed"].format(n=len(fields)) if fields else t["none"], "items": items})
    uncertain = [t["uncertain"], t["missing"]]
    if any(p["method"] == "ocr" and (p["ocr_confidence"] or 0) < 0.6 for p in pages):
        uncertain.append(t["ocr"])
    sections.append({"title": "What is still uncertain", "summary": " ".join(uncertain), "items": []})
    sections.append({"title": t["q_title"], "summary": "", "items": [{"text": q} for q in t["questions"]]})
    spoken = " ".join([sections[0]["summary"]] + [i["text"] for i in items[:10]] + [t["uncertain"], t["review"]])
    return {
        "document_id": document["id"], "language": lang, "speech_language": SPEAK_LANG[lang],
        "status": "clinician_review_required", "clinician_review_message": t["review"],
        "sections": sections, "spoken_text": spoken,
        "disclaimer": "This is a plain-language summary of what your document says. It is not a diagnosis.",
    }
