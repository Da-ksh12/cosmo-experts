"""The secure agent: a rule-driven assistant that answers from the signed-in person's own records and the clinic's approved knowledge by calling a small set of typed tools.

    request -> safety classification -> intent -> tool calls (permission checked, validated, bounded) -> evidence -> composed answer -> safety review -> trace

Design rules (docs/AI_AGENT_AND_TOOL_SECURITY.md):
* The person is always the authenticated session; no tool takes a user id. Records are fetched with the session's id in the query, so another person's id is indistinguishable from a missing one.
* Tools are a closed, typed list. There is no generic query, shell, file or URL capability.
* Reading and changing are separate: a tool that changes state never runs from a chat turn. It is proposed, shown to the person in plain words, and runs only when the person confirms it through
  a separate authenticated request. Nothing a document says can make that request.
* Retrieved text is data. It is quoted, with citations; it never selects a tool, fills an argument or reaches a model as an instruction.
* The agent does not diagnose, prescribe or decide urgency. Those requests get the shared refusal or the approved urgent wording, before any tool is called.
"""
