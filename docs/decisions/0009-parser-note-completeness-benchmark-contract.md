# ADR-0009: Parser and Note Completeness Benchmark Contract

## Status

Accepted at the approved contract and topology boundaries; the benchmark-only `NormalizedDocument` v1 schema, validation, canonical serialization, and digest boundary are realized. Fixture evidence, other schemas, metric-specific policy, calibration, and later implementation remain pending.

## Context

LearnLoop needs a reproducible evaluation of source parsing and grounded-note
completeness. This ADR preserves the design boundaries without making the full
local record a dependency. The contract is `parser-note-completeness-v1`.

## Decision

### Scope

The benchmark has three independent lanes:

1. **Parser** evaluates raw source to a normalized parser artifact.
2. **Generation** evaluates a frozen generation reference document to a
   renderer-neutral note artifact.
3. **End-to-end** evaluates raw source through the final rendered-note
   projection.

Each lane owns its own inputs, evidence, results, blockers, and gate closure.
A result in one lane cannot compensate for a failure in another lane.

The benchmark does not evaluate or authorize retrieval, embeddings, `top_k`,
query relevance, RAG ranking, Step 100, a production rollout, or a runtime
schema change.

### Fixed case topology

The full profile contains 13 logical cases across five source families:

| Family | Cases |
| --- | --- |
| PDF | `P01`–`P04` |
| Web | `W01`–`W03` |
| YouTube | `Y01`–`Y02` |
| Chat | `C01`–`C02` |
| Screenshot | `S01`–`S02` |

This fixes the logical topology, not a dataset. The Q22 fixture slate remains
`evidence_required`. Its creation plans are
candidate plans, not an approved dataset. No case becomes canonical until its
exact bytes, digest, provenance, rights and privacy evidence, and independent
approval exist.

`smoke` is exactly `P01`, `W01`, `Y01`, `C01`, and `S01`. It references the
full manifest's same canonical bytes, fixture revision, digest, gold/reference,
and compatible contracts; reduced or smoke-only fixtures are forbidden.
Smoke is always `diagnostic_only` and has no baseline, comparison, gate, or
adoption authority. Only the complete 13-case `full` profile supports formal
baseline, comparison, and adoption.

### Benchmark artifact boundaries

`NormalizedDocument` is the Parser lane boundary. Its top level is exactly `schema_version`, `artifact_role`, `document_id`, `source`, `capabilities`, `sections`, `elements`, and `producer_provenance`. `artifact_role` is `parser_output` or `reference_document`; source records type (`pdf`, `web`, `youtube`, `chat`, or `screenshots`), identity, display name, snapshot SHA-256, and ordered, deduplicated languages. Source and element languages reject `mixed` and use `und` when unknown.
The required capabilities are hierarchy, language identification, geometry, table structure, code metadata, source modality, and typed locators. Each is a typed `available`, `partial`, `unavailable`, or `not_applicable` declaration; `partial` and `unavailable` require a machine-readable reason.
The closed element kinds are `heading`, `paragraph`, `list_item`, `quote`,
`code_block`, `table`, `table_row`, `table_cell`, `figure`, `caption`,
`formula`, `transcript_segment`, `message`, `ui_text`, `page_break`, and
`unknown`. Sections bind unique IDs, optional parents and headings, and inclusive element-order ranges. Elements bind unique IDs, kind, section, optional parent, source-faithful content, languages, locators, applicable typed metadata, and globally unique, zero-based, gap-free `order`; the array itself follows that order.

Locator availability is separate from platform-identity provenance: a YouTube locator may be `available` with cue/timestamps while `video_identity` and `caption_track_identity` are independently `available` or typed `unavailable`; an unavailable locator carries no cue, timestamp, or identity. Available PDF, Web, Chat, and Screenshot locators require their family identities. Geometry uses top-left-origin integers in the named normalized space `0..1_000_000` and cannot exceed it. Producer provenance records producer identity/version, configuration digest, segmentation semantics, processing method/stage, and optional parser/OCR/ASR model identity.

`document_id` is benchmark-manifest assigned. Element IDs are stable only
within the same artifact and segmentation semantics; they do not claim
cross-parser identity. The artifact excludes chunks, embeddings, retrieval
scores, `top_k`, evidence importance, gold, expected claims, volatile execution
facts, and its own digest.

The generation reference is frozen and byte-identical per benchmark revision.
Gold/evidence/expected claims are separate human-governed artifacts. The
`BenchmarkNoteDocument` is the renderer-neutral Generation boundary; its
rendered projection comes from authoritative output or verified readback.
`GenerationCoveragePlan` is immutable, exhaustive, and pre-capture;
`GenerationRoutingPolicy` deterministically selects a mode from preregistered
facts. Manifests bind revisions and slots; receipts record attempts and
conformance; formal revisions are immutable and content-addressed. These are
benchmark artifacts, not production or runtime schema extensions.

### Authority and decision ownership

Authority and outcome concepts remain separate records:

| Record | Owns |
| --- | --- |
| `authority_status` | Whether evidence is permitted to support the governed decision scope |
| `result_role` | Exactly `formal` or `diagnostic_only` for the result's declared role |
| `quality_decision` | Exactly `pass`, `hard_blocked`, `aggregate_gate_failed`, or `not_evaluated` |
| Gate decision | Closure of the preregistered gates and blockers for its scope |
| Comparison outcome | Candidate-versus-baseline result for eligible paired evidence |

No implementation may collapse these records into a single super-status.
Operational completion is also distinct from every record above.

`provisional`, `invalid`, and legacy `inconclusive` belong to Q10
validity/authority governance, not `result_role`. Q15 run membership
(`formal_required` and diagnostic semantics) is a separate record, not the Q13
`result_role`.

An `unresolved` item must remain visible and scoped to its owner. It must not
silently become a zero score, failure, success, exclusion, denominator change,
or comparison result.

Only deterministic backend logic calculates formal scores and decisions from
approved evidence. LLM assistance cannot approve gold, resolve disputes, alter
policy, or act as acceptance judge.

### Scoring and comparison

Formal coverage is measured over the authority-closed expected-claim
denominator using independent exact counts and exact rational rates for
`fully_covered`, `partially_covered`, and `not_covered`. These are the three
decided coverage states used for formal exact-count and exact-rate calculation.
`unresolved` remains a Q8 coverage state only in provisional/audit records; it
is not a numerator, zero, failure, success, or exclusion. Applicability and
exclusion belong to Q12 typed denominator disposition, not coverage state.
No combined scalar or numeric partial credit is produced.

Claim support is reported with exact counts. Support rates may be diagnostic,
but they do not dilute unsupported or contradicted claims and are not formal
authority by themselves.

Evidence is kept in separate `critical`, `major`, and `minor` strata. The
strata do not imply numeric importance weights.

Version 1 has no global composite, importance-weighted aggregate, universal
macro or pooled micro authority, numeric partial credit, or subjective LLM
readability score.

Metric-specific aggregation is allowed only after its owner selects and
documents an evidence-supported contract.

Absolute quality and comparative results are separate. A comparison cannot
turn a candidate that fails an absolute gate into an acceptable result.

When exact zero is a valid baseline or candidate result, comparison reports
the metric-native absolute change and treats undefined relative change as
undefined. It must not add epsilon, report infinite improvement, or invent a
finite multiplier.

Missing or ineligible pairs remain visible and prevent required comparison
closure; they are not silently dropped.

The 13 fixtures are a fixed conformance suite, not a production population.
Bootstrap is not enabled in v1. Population inference, population CI, p-value,
statistical significance, and a formal statistical gate are also not enabled;
none is current `pending_calibration`. Repeated-run diagnostics have no
adoption authority and cannot compensate for any formal hard blocker.

### Blockers and gate topology

A critical omission is a hard blocker within its applicable scope.

Every critical expected claim that is not `fully_covered`, including
`partially_covered` and `not_covered`, is a hard blocker. A formal,
adjudicated generated claim marked `unsupported`, `contradicted_by_source`, or
`overstated` is also a hard blocker. `candidate_internal_contradiction` is an
independent consistency relation, not a Q8 support enum, and is hard-blocked
under Q12. Citation cannot compensate for source-support failure.

Provably fabricated page, cue, message, image, or DOM locator identity is a
hard blocker. A missing or typed `unavailable` locator is not fabrication.
`partially_supported` is hard-blocked only when adjudication finds it cannot be
split and contains a substantive unsupported component; an unresolved dispute
stays in unresolved governance.

Blockers cannot be averaged away, offset by aggregate improvement, or
compensated by another lane, fixture, run, or importance stratum.

Parser, Generation, and End-to-end each require their own gate closure. A
formal adoption decision requires closure for every applicable lane and scope.

Parser critical dependency is derived only from approved support expressions:
`all_of` missing any required leaf fails the dependency, while `any_of` needs
one complete approved alternative to avoid a critical blocker. Other omissions
remain parser completeness loss; the scorer must not infer new dependencies.

The exact numeric gate constants remain owned by Q11 calibration. This ADR
sets no threshold, weight, epsilon, partial-credit value, time ceiling,
resource ceiling, or cost ceiling.

Canonical JSON uses UTF-8, sorted keys, compact encoding, and LF, with no NaN
or Infinity. Array order is semantic; the canonical payload excludes its own
digest. Whole-document `order` is unique, continuous, starts at zero, and is
the `elements` array order. Missing locators use typed `unavailable`; IDs do not
claim cross-parser identity. Unproved alignment must abstain, not fuzzy-match.

### Runner, offline execution, and reproducibility

Canonical validation, scoring, aggregation, comparison, and replay must run
offline inside an OS/container-enforced no-egress boundary. Mock transports or
missing credentials do not prove offline behavior; a network-denial conformance
record is required, and canonical processes reject live flags and
credential-bearing inputs.

One versioned runner CLI emits machine-readable terminal JSON status for every
runner-controlled terminal outcome and references an immutable terminal
package. Exit `0` means schema-valid completion (quality may still pass, fail,
or hard-block); exit `1` means operational failure or required work
incomplete; exit `2` means the input or execution contract was rejected or
invalid. Codes do not replace Q10-Q15 records. External `SIGKILL`, host loss,
or equivalent termination may have no terminal package or exit code; retain
immutable partial history and reconcile later without fabricating either.

Provider-backed capture, when later authorized, is a distinct preregistered
phase with approved frozen input; canonical scoring remains offline.

Each planned execution slot has immutable attempt history. Retry and resume may
append attempts only to original open slots under the unchanged plan and
contract digests.

Run identity binds the profile, benchmark revision, plan, contract versions,
candidate identity, and execution mode. Formal and diagnostic membership must
remain separate before outputs are observed.

Scoring replay consumes captured immutable artifacts and produces no new
stochastic run or sample. Replay must preserve the original collection and
attempt identities.

Reproducibility records capture provider and model revision, seed value and
capability, approved code or build identity, dependency and runtime facts,
allowlisted configuration digests, platform and execution-contract facts, and
artifact, manifest, receipt, and result digests.

When provider/model revision or seed capability cannot be observed, record
explicit `unavailable`. Prompt contract and digest are replay provenance.
Receipts must not copy secrets, complete prompts, private raw source content,
credential-bearing configuration, or unrestricted environment dumps; private
content may exist only within its approved immutable artifact boundary.

Duration, input and output size, retries, usage, cost, CPU, memory, and
availability are preserved as raw facts when observable. Resource observations
do not become quality gates without a later calibrated Q11 decision.

### Fixture governance and storage

Canonical fixtures follow a project-owned, synthetic-first policy. A fixture
may be eligible only when redistribution, privacy, provenance, digest, and
review evidence are explicit.

Chat and Screenshot remain distinct source families even when both use
project-owned synthetic fixtures. Screenshot fixtures do not become Chat
fixtures merely because visible text can be transcribed.

Synthetic caption fixtures use typed `unavailable` for unavailable YouTube
identities rather than invented provenance; v1 scores neither audio nor ASR
quality.

The canonical tracked root is `tests/evals/parser_note_completeness/v1/`, with
fixed subdirectories `fixtures/`, `governance/`, `reference_documents/`,
`gold/`, and `manifests/`. Acquisition receipts are fixture-governance
revisions; no generic tracked `receipts/` directory is created. Run
receipts/results are independent artifacts and their exact formal result-store
realization remains pending. Local diagnostics use
`local_storage/benchmarks/parser_note_completeness/v1/`. Formal manifests must
not reference local diagnostic artifacts.

Fixture creation, rights/privacy review, gold annotation and review, scorer
approval, and gate approval follow separation of duties; nobody independently
approves their own work for the same scope. Missing fixture evidence prevents
eligibility, missing gold review keeps gold non-authoritative, and missing
scorer/gate/governance approval prevents that closure.

### End-to-end and long-source behavior

The renderer-neutral note artifact is the stable comparison boundary before
renderer conversion. It does not authorize a production proposal or Notion
write.

`BenchmarkNoteDocument`, `GenerationCoveragePlan`, and
`GenerationRoutingPolicy` are working labels; exact schema identifiers remain
pending. The coverage plan binds the exact Q5 reference digest. Routing's
closed modes are `single-pass`, `section-aware`, and `hierarchical`. A formal
route mismatch is an execution-contract/conformance failure, not a low-quality
score; unknown provider capacity is explicit `unavailable`.

End-to-end has two distinct views:

1. **Final rendered quality** compares the rendered-note projection with
   approved gold and reference evidence.
2. **Renderer preservation** compares the pre-render note artifact with the
   rendered-note projection to locate renderer-origin loss or fabrication.

These views remain part of the End-to-end lane. They do not create a fourth
renderer lane.

Long-source generation requires an exhaustive pre-capture plan with one primary
scoring-owning assignment per source unit; overlap cannot create duplicate
credit. Work-unit outcomes, merge lineage, omissions, conflicts, and closure
are immutable and missing work cannot be silently replaced.

Routing must be decided before the first model call using the registered
`GenerationRoutingPolicy`. The decision must not inspect candidate output or
quality.

Forced-mode runs are allowed only as preregistered diagnostics. They cannot be
promoted into formal evidence after their outputs are observed.

Long-source planning and routing do not introduce retrieval, embeddings,
ranking, section selection by relevance, `top_k`, or Step 100 behavior.

## Pending frontier

The following work remains explicitly pending:

- Q22 fixture bytes, digests, provenance, rights and privacy evidence, and
  independent approvals;
- remaining schemas, enums, manifests, receipts, and result-store realization;
- parser-specific unit inventories and metric definitions, including
  normalization, alignment, locator, table, formula, OCR, and caption policies;
- evidence-supported aggregation selections for each applicable metric;
- quality thresholds and measurement-boundary calibration under Q11;
- repeat count, scheduling or block design, seed policy, run-level pairing,
  execution compatibility, and diagnostic-method activation;
- long-source work-unit sizing, overlap, merge, conflict detection, and
  coverage-closure rules;
- routing boundaries, provider-capacity handling, and routing compatibility;
- implementation, full baseline capture, calibration evidence, and canonical
  replay evidence.

These items must not be inferred from this ADR or filled with provisional
numeric values in formal artifacts.

## Suggested implementation order

When implementation is separately authorized, proceed in this order:

1. Build one minimal project-owned synthetic vertical slice.
2. Add scorer and runner skeletons around immutable artifacts and replay.
3. Wire the five-case smoke profile with one case per source family.
4. Complete all 13 cases, capture a baseline, and perform calibration before
   any formal candidate decision.

This sequence is an implementation plan only. It does not approve fixtures,
choose pending schemas or metric policies, set numeric constants, or convert
uncalibrated evidence into a frozen decision.

## Consequences

Future work has a tracked contract independent of the local foundation. Clone,
runtime, tests, CI, and formal artifacts must not depend on that local record.

This separation prevents lane compensation, post-hoc dataset changes, hidden
unresolved states, and renderer or routing effects being confused with parser
or generation quality. It authorizes no implementation, production change,
fixture approval, threshold, baseline claim, or Step 100 work.
