# Offline recorded/synthetic evaluation

Run `.venv/Scripts/python.exe scripts/evaluate_offline.py` from Version 2.
The runner imports validators only: no GPT client, database queries or writes.
Fixtures are explicitly synthetic recorded responses, not production exports.

Software checks cover all-Pass/nonblocking routing, essential Unknown, a recorded
scope failure, unsupported quotation detection and untrusted document instructions.
The last case is not proof that a live GPT model resists prompt injection.

Keep expert judgement separate: status, reviewer, accuracy and notes are unapproved
until an actual domain expert reviews a case. Do not turn software pass counts into
underwriting accuracy or model-quality claims. Future recorded responses should
carry source provenance and full prompt/model versions without credentials. Do not
paste private broker data into this tracked fixture directory.
