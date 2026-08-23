# Communications Autopilot and Human Briefing

VAAPP treats communication handling as VA-owned work by default. Protected/sensitive evidence is separate from actionability: OTPs, routine financial confirmations, tickets, delivery updates and informational notices do not become Needs You work merely because they mention money, security or verification.

High-confidence mailing-list/promotional Gmail messages can go straight to Trash even when unread. Mailing-list unsubscribe language is not evidence of a paid subscription. Provider/AI defects stay system-owned and their raw error text is kept in diagnostics rather than assigned to the user.

Immediate Android alerts require an explicit `interrupt` decision, currently reserved for genuinely urgent communication such as credible fraud/security wording. Routine execution is reported in scheduled, narrative VA briefings. Morning, afternoon and evening periods are independently configurable; defaults remain morning off, afternoon off and evening on.

Android SMS history includes MMS text backfill and a person/thread-based conversation view. The app requests up to 1,000 communication events for that view. Newly received high-confidence promotional SMS can be removed from the Android SMS store only when VAAPP is the default SMS handler and the backend explicitly returns `delete_from_device=true`.

Legacy communication tasks, stale OTP work, duplicate task projections and provider-error tasks are repaired without deleting source evidence. Manual completion is restricted to genuinely manual/physical tasks; provider work remains complete only after independent verification.
