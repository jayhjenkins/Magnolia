# Ticket Impact & Severity — Reference Tables

Source: Vantaca Customer Experience / Customer Support "Policies & Expectations" guidelines. Verbatim reference for `workflow-bug-severity-assessment`.

## Severity

Initial ticket severity is created in Zendesk and populates into Jira severity when the issue is escalated. Severity 1/2 trigger an automated Teams "Urgent Issues" notification.

| | Severity 1 | Severity 2 | Severity 3 | Severity 4 |
|---|---|---|---|---|
| **Definition** | One or more core applications is not functional / accessible across all clients | One or more critical business processes is not functional for one or more clients **without viable workaround** | Application is not functioning as expected. Intermittent issue. **Workaround is available** or function is not critical | Application is not functioning as expected. Issue is low impact and does not impact everyday use |
| **Examples** | Vantaca.net unreachable; no user can login to IQ; mobile apps not connected to Vantaca; banking integration failing (large volume); ACH/Lockbox; paying invoices not available; emails/notifications not sending | (critical process down, no workaround) | Buttons on billing screen don't show correct information; click-pay deposits combined by the bank | Data changes for historical information; sorting not functioning; searching provides unnecessary results |
| **First Response Time** | 30 minutes | 2 hours | 4 hours | 8 hours |
| **Resolution** | Worked until resolved | Worked until resolved | Prioritized by Product Management & Support | Prioritized by Product Management & Support |
| **Internal Communication** | Urgent Teams Channel notification + Jira link; summary updated every 2 hours | Urgent Teams Channel notification + Jira link; summary updated daily | Via product management, based on backlog(s) managed in Jira | Via product management, based on backlog(s) managed in Jira |
| **Customer Communication** | Mass Communication Process for Bug | Mass Communication Process for Bug | Mass Communication Process for Bug | Mass Communication Process for Bug |

Decision rule: match the ticket's actual behavior to the **Definition** first; use **Examples** to disambiguate close calls. The Sev 2 vs. Sev 3 boundary turns entirely on whether a **viable workaround exists** — if one does, it cannot be Sev 2 regardless of how important the affected process is.

Out of scope for this skill: the guidelines also define an Impact scoring matrix (7 categories, 1-4 pts each, for Sev 3/4 tickets) set by the Product team. This skill deliberately does not compute or write Impact — severity classification only.
