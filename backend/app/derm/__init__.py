"""Dermatology and skincare education layer.

* pack.py      - the versioned, source-traceable knowledge pack (content/derm): schema, validation, deterministic rendering, seeding into the clinic's review table.
* reference.py - which tier an article is in right now (clinic-approved, or source-attributed but awaiting clinician review) and retrieval over the pack.
* lexicon.py   - what a message is about (topic, duration, symptoms) in English, Hinglish and Hindi.
* triage.py    - warning signs: how urgently a described problem should be looked at, and why.

Nothing in this package diagnoses, prescribes or reads an image. It explains general information and says where it came from."""
