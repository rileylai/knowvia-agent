# Runtime Prompt Templates

The prompt loader reads the following files from this directory:

| Prompt id | Active file | Used for |
| --- | --- | --- |
| `qa_answer` | `qa_answer_v2.md` | Grounded QA answer generation |
| `supplement_proposal` | `supplement_proposal_v7.md` | Supplement proposal generation |
| `screenshot_body_repair` | `screenshot_body_repair_v2.md` | Bounded screenshot proposal body repair |
| `screenshot_title_repair` | `screenshot_title_repair_v3.md` | Bounded screenshot title repair |
| `screenshot_summary_repair` | `screenshot_summary_repair_v2.md` | Bounded screenshot summary repair |

Each template uses frontmatter for `prompt_id` and `version`, followed by
`## System` and `## User` sections. Prompt changes are runtime changes: keep
the version explicit and run proposal, QA, prompt-safety, and redaction tests.

Prompt text is not an authorization layer. Backend code still owns target
selection, citations, write policy, output validation, and review state.
