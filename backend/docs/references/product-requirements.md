# Product requirements summary from Google Docs

Source: [SalesMate MVP feature set, September 9 update](https://docs.google.com/document/d/1IG0NtzszF1-RVtFgIuH_4KKTUuhKrehARNn_H6gt3aQ/edit). Text was read from the authorized page and summarized on 2026-09-11. This is an offline summary, not a mirror; prototype images/demo-folder contents were not archived or verified.

Return to [project references](../project-reference.md).

## Page A: Company-grouped Gmail inbox

Purpose: help salespeople identify customers requiring priority attention.

| Area | Features |
|---|---|
| Header/channel entries | Global insights/social intelligence, sync state, user information; Gmail available, four other channels disabled as placeholders |
| Overview | Company-group count, unregistered customers, today's new emails |
| Company rows | Name, initial avatar, domain, contacts, email count, latest time, CRM registration, latest-message summary |
| Analysis tags | Industry, size, business signals, AI follow-up priority score/progress bar |
| Queries | Industry/size/signal filters, individually resettable; descending priority by default |

Specified industries: semiconductor inspection, precision metrology, optical inspection, industrial inspection.

Size labels: 50–100, 100–200, 200–500, 500+ employees. Boundary ownership and below-50/unknown display require alignment with Agent enums/APIs.

Revised signals have four categories: new unregistered leads, inquiries, quoted but not closed, repeat purchases. All is a filter operation, not a signal. Open opportunities was removed by the revision.

## Page B: Three-column customer workspace

| Area | Features |
|---|---|
| Left: Emails/replies | Timeline, all/inbound/outbound filters, expandable Chinese translation, reply drafts; distinguish empty, filled-unsent, and sent |
| Center: Customer information | Company introduction/tags, profiles, AI analysis, opportunities/orders, contacts; automatic analysis on detail opening, unknowns grayed out |
| Right: AI assistant | Conversations, steps, files, actions grounded in emails, customer, opportunities, orders, knowledge |

The explicit later revision defines three textual profile dimensions:

- Industry situation.
- Company operations: size, decisions, procurement, historical cooperation, etc.
- Intent analysis.

AI analysis has four dimensions: timeline, opportunities, risks, and guidance suggestions. Remove purchase-probability percentages and explain evidence/missing information in prose.

Five assistant shortcuts cover history/needs, draft replies, advancement plans, comparison materials, and risk analysis, alongside free input. Requirements also propose recent industry news to explain motivation and help unregistered leads identify missing information, follow-up value, and first-response strategy.

## User operations and state constraints

1. Authorize/synchronize Gmail and show company-grouped emails.
2. Filter companies, open details, and read correspondence/profiles/analysis.
3. Assistant generates suggestions/replies; users confirm insertion into drafts.
4. Users send and see actual results, times, and content.
5. Only successful real sending permits agreed extracted fields to be written back.

Distinguish suggestion generation, draft insertion, send request, successful sending, and completed writeback. Generated text is not completed external action. Exact sending/writeback APIs remain to be designed in this source baseline.

## Internal conflicts and differences across documents

| Source condition | Summary treatment |
|---|---|
| Early filters retain open opportunities, later explicitly removed | Use four valid signals; treat old wording as cleanup residue |
| Early UI/output tables retain six profile fields/probability, later revised | Use three profile/four analysis dimensions without percentages; team must reconcile originals/final fields |
| Lists show AI scores while details remove purchase probability | Different meanings: follow-up priority is not purchase probability |
| Product includes knowledge, news, assistants, translation, sending | Agent v1.11 excludes them from module MVP; retain product intent without automatically assigning backend/Agent ownership |

This file records product intent. Start from [current progress/open alignment](../project-reference.md); exact fields, enums, permissions, and transitions must enter concrete contracts.
