# Retired review views

The monday human-verification loop was retired on 2026-08-13. The runtime now
writes extracted values as final, marks affected records `Verified`, and finishes
Meetings as `Processed`. Listening-quality review happens only in the originating
Slack thread through `score:` and `feedback:` replies.

Historical review views may still exist in monday, but the runtime no longer
creates, populates, or depends on them. They can be hidden or deleted in monday
without affecting processing.
