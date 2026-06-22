# Bug Report

_Hand-curated from reviewing call transcripts. AGENT = Pretty Good AI test line (the AI under test); PATIENT = our automated tester._

> Note: `make report` / `make analyze` regenerates this file via Claude and will **overwrite** these hand-written entries. Keep this curated copy if you edit by hand.

---

Bug: Accepts a failed identity verification and proceeds anyway
Severity: High
Call: call-01-schedule_simple.txt at 1:01
Details: The patient gave a date of birth (March 4th, 1990). The agent responded
"A birthday doesn't match our records. But for demo purposes, I'll accept it,"
and continued. In a real medical practice this is a privacy/safety red line: it
should not advance an identity-gated flow when verification fails (re-prompt,
escalate, or refuse) — anyone supplying a wrong DOB would clear the check. It
also leaked an internal "for demo purposes" framing to the caller, which should
never be spoken to a patient.

Bug: Hallucinated an existing appointment for a self-declared new patient, blocking a legitimate booking
Severity: High
Call: call-01-schedule_simple.txt at 0:34–1:14
Details: The patient explicitly asked for a routine new-patient appointment. The
agent first said "your last visit info is not available," then minutes later
claimed "you already have an appointment of that same type, so I can't book
another duplicate one right now," and refused to schedule. This both invents a
record that was never established and contradicts its own earlier statement that
there was no prior visit info. The result is that a valid new-patient request
could not be booked at all.

Bug: Promised a live-agent transfer, then dead-ended and hung up without resolution
Severity: Medium
Call: call-01-schedule_simple.txt at 2:21–2:28
Details: The agent said "Connecting you to a representative. Please wait," then
looped back to itself ("Hello… You've reached the Pretty Good AI test line.
Goodbye.") and disconnected, leaving the booking unresolved. The agent committed
to an action (human transfer) and then abruptly terminated the call with no
hand-off and no fallback. Caveat: the inability to reach an actual human may be a
limitation of the assessment test line rather than the agent's logic; the
reportable defect is promising a transfer and then dead-ending the caller. This
transfer was itself only reached because of the hallucinated-duplicate bug above.
Reproduced even more bluntly in run 20260621T171504 at 1:22–1:38: after
"Connecting you to a representative. Please wait," the line answered "Hello.
You've reached the Pretty Good AI test line. Goodbye." and hung up while the
patient was still mid-request ("…I got disconnected from the scheduling. Hello?").

Bug: Never collects the caller's name; "verifies" identity on date of birth alone
Severity: High
Call: call-01-schedule_simple.txt at 0:21–0:32 (run 20260621T171504); see also 1:01
Details: The only identifying detail the agent ever asks for is date of birth
("Please tell me your date of birth"). It never requests the caller's name, yet
immediately proceeds to look up records and discuss appointments. When the patient
volunteered a name unprompted in another call ("my name is Jordan Lee," run
20260621T162935 at 2:37), the agent neither confirmed nor used it. Combined with
accepting a DOB that "does not match our records" (see the failed-verification bug
above), the agent makes account-level decisions with essentially no reliable
identity — one often-mismatched data point and no name at all. This is a
safety/privacy problem and the likely root cause of the phantom-appointment
confusion: it cannot actually tell who it is talking to.

Bug: Opening greeting contains an unresolved name placeholder ("Am I speaking with New?")
Severity: Medium
Call: call-01-schedule_simple.txt at 0:08–0:11 (every call; kept evidence in runs 20260621T171504 and 20260621T162935)
Details: The agent's identification line consistently ends with "Am I speaking
with New?" in every single call. "New" reads as an unpopulated template variable
(a first-name / "new patient" slot that never resolved), so the very first thing
the caller hears is a literal placeholder instead of a real name or a clean
question. The turn also frequently trails off right there — a multi-second pause
after "New" with no completion — making the greeting sound broken. Poor, robotic
first impression and a sign of a templating/personalization defect.

Bug: Repeats and restarts its own prompts mid-turn
Severity: Low
Call: call-01-schedule_simple.txt at 0:32–0:38 (run 20260621T171504)
Details: The agent began "I see your last visit was not available in the chart.
Like to book an office visit today?", emitted a stray "I", then restarted and
re-said the whole thing: "…your last visit was not available in the chart. Would
you like to book an office visit today?" The self-interruption and near-verbatim
restart (with our tester silent throughout) makes the agent sound glitchy and
forces the caller to sit through duplicated speech.
More of the same in call-01-reschedule.txt (run 20260621T181307): it duplicated a
whole prompt back-to-back — "Please provide your date of birth. Please provide
your date of birth." (0:27) — and produced an ungrammatical confirmation,
"You should have to text shortly." (4:33; verified by listening — the agent
really said it, garbled, where it meant "you should get a text shortly").
And again throughout call-01-interruptions.txt (run 20260621T221816), where it
spoke whole prompts twice back-to-back: "Please provide your date of birth.
Please provide your date of birth." (0:25) and "Please provide your full name,
first and last, and your date of birth." repeated verbatim (0:41). Consistent
stutter/restart/duplication across calls.

Bug: Stuck in a verification loop — re-requests details already given and confirmed, never advances
Severity: High
Call: call-01-interruptions.txt at 0:25–2:54 (run 20260621T221816)
Details: The agent repeatedly asked for the same identifying details and never
retained them, so the call never reached the actual task (scheduling). Date of
birth was requested at 0:25, 0:41, and 2:46; the patient supplied it four times
(0:29, 1:04, 1:29, 2:54). Phone number was requested at 1:39, again at 2:25 ("give
me the phone number on your file again"), and again at 4:22. The clearest failure:
at 2:04 the agent read the phone number and DOB back and asked "Is that correct?",
the patient confirmed (2:18) — then at 2:25 it asked for the phone number again
and at 2:46 for the date of birth again. It confirms a detail and immediately
re-requests it. The caller is trapped re-supplying the same data and the booking
is never completed (the call again dead-ends to "our clinic support team will
follow up"). This loop, not any single prompt, is the dominant failure of the call.
