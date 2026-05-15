---
name: "\U0001F680 Feature Request"
about: Suggest a new bot feature, job, or webhook-triggered behavior for the Telegram bot
title: "[Feature] <Concise Feature Title>"
labels: [feature, enhancement]
assignees: []

---

## Summary

**Describe the feature, job, or automation you want to add.**
- What kind of update should trigger it? (message, file, command, etc.)
- Is it user-facing, admin-only, or both?

## Usage Example / User Flow

> E.g. "When a user sends a photo, save it to GCS and reply with a thumbnail."  
> Or: "Allow `/remind 13:00 take out the trash` to schedule a reminder."

- Example incoming update(s):
  ```
  <Describe or provide sample JSON of the update(s) that should trigger this feature>
  ```
- Desired bot behavior/response:

## Tasks

- [ ] Parse relevant update(s) and route to this job/feature.
- [ ] Implement handler logic.
- [ ] Store/update data if needed. (e.g. BigQuery, GCS)
- [ ] Tests for routing, handler, and expected outcomes.
- [ ] Docs: usage, edge cases, config.

## Acceptance Criteria

- [ ] Feature is reachable via correct update, command, or file.
- [ ] Incorrect/unsupported usage is handled gracefully.
- [ ] Core flows covered by tests, pytest check all green.
- [ ] Lint/format checks by ruff succeed.
- [ ] No secrets/personal data leaked in logs or responses.
- [ ] README.md, SPEC.md and relevant GitHub issues are updated to reflect the bot's real functionality following the implementation.

## Risks & Limitations, Stuff Explicitly Out of Scope and Future Work

- (Optional) Risks foreseen, limitations of certain approaches, stuff to keep in mind in the future
- (Optional) Related features or edge cases for later.

---

**Relevant Spec/Docs:**  
- [Project SPEC.md](../SPEC.md)
- [Architecture Docs](../docs/architecture.md)
