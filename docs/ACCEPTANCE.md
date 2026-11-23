# 0.1.0 acceptance ledger

**Status: NOT ACCEPTED.** This records observations, not a release declaration.
S28 remains InProgress until the mandatory smoke and full gate pass. Automated
checks do not substitute for the study lead's manual or scientific sign-off.

## Result checks and second-round fixes — 2026-11-22

**Automated coverage only; nothing here is accepted.** The rows record which
tests cover the result checks (flags, taints in more cases, stop rules, the
judge reader, the health summary, the pages and the documents) and the fixes
of the second round of the designed-study work. They were observed on the
uncommitted working tree of the branch `experiment-design-and-study-map`,
while other work was being done in the same tree. Every stop rule was
exercised against the stub engine and the stub judge. No row is a manual,
live-hardware or scientific sign-off.

| Part | Covered by | Observed 2026-11-22 |
|---|---|---|
| The catalogue: the detectors against the pinned texts, the judge reader against the pinned probe replies, the lists of evals, every clause of every stop rule in the `Breaker`, what a resumed run counts again and what it does not | `.\backend\tests\test_result_checks.py` | Passed |
| Records and the health summary: `ResultFlag`, `Halt`, the fields a file leaves out while they are empty, counts per arm and eval, the cautions, a run made before result checks | `.\backend\tests\test_health.py`, `.\backend\tests\test_store.py` | Passed |
| The runner raises the flags and the taints: `flags.jsonl`, flags on `sample.finished`, a stream without a finish reason, a malformed stream line, a fallback in the token records, a missing token log, taints written before the reply, a flag excludes nothing | `.\backend\tests\test_run_flags.py` | Passed |
| Stops: each rule against the stub engine and the stub judge, the stored `Halt`, one `check.stopped` event, the state `paused`, resume as a new pass, a second stop after a resume, a restart between stop and resume, a resume refused while requests are still finishing, a host resume that records nothing, `checks_version` at the first start only, Inspect samples, supervised sittings | `.\backend\tests\test_run_stops.py` | Passed |
| Judge phase: the four statuses, a received reply is final, only a judgement without a reply is asked again, the flags of a judgement, `judge_fields` | `.\backend\tests\test_judge_phase.py`, `.\backend\tests\test_judge_fields.py` | Passed |
| The exclusion rule of every plan: a retried error is analysed, both ways of loading the clean data agree, no other taint is lifted; the legacy pins and the thesis study are untouched | `.\backend\tests\test_exclusion_rule.py`, `.\backend\tests\test_legacy_bytes.py`, `.\backend\tests\test_thesis_study.py` | Passed |
| API: `GET /runs/{id}/health`, `halts` and `checks_version` on run and study payloads, the per-run counts, the provenance rows of the analysis index, a stop holds no key | `.\backend\tests\test_api_run_health.py` | Passed |
| Designed pre-registration: the section "Result checks" is printed from the catalogue; the pinned designed document; a study without a design block has no such section and keeps its bytes | `.\backend\tests\test_prereg_result_checks.py`, `.\backend\tests\test_prereg.py` (among others `test_designed_golden_bytes`, `test_deterministic_and_golden_bytes`) | Passed |
| Documents: `ANALYSIS_PLAN.md`, `SETUP_GUIDE.md`, `API.md` and `add-an-eval.md` say about the checks what the catalogue and the records say; no analysis module reads a flag, a caution or a stop | `.\backend\tests\test_docs_result_checks.py` | Passed |
| Second round, permutation count: the largest of 10 000, 20 · difference tests / α and 2 · seed-pair tests / α; refused above the cap. This replaces the row "Permutation count" of the table below, whose rule was changed | `.\backend\tests\test_design.py`: `test_control_permutations`, `test_control_family_sizes_the_permutations`, `test_permutations_scale_and_cap` | Passed |
| Second round, control floor: at the floor a seed pair that differs on every prompt fails in at least a third of 20 analysis seeds, three prompts above it in all of them, two below it in fewer; the texts say that it is not certain the control can fail. This replaces the wording of the row "Negative-control floor" below | `.\backend\tests\test_power.py::test_a_seed_pair_can_fail_at_the_control_floor`; `.\backend\tests\test_design.py::test_control_floor_is_a_problem_of_the_design`; `.\backend\tests\test_prereg.py` | Passed |
| Second round: a level outside its allowed values is a `400`; a study on the built-in plan is not blocked for its comparison; a token-level outcome with no planned data is a problem | `.\backend\tests\test_config_parse.py`; `.\backend\tests\test_design.py`: `test_a_study_on_the_built_in_plan_is_not_blocked_for_its_comparison`, `test_token_stream_outcomes_with_no_planned_data_are_a_problem` | Passed |
| Web, result checks: the "Stopped by a check" notice and its key form, the line on the live page, "Output health" on the run and results pages, the taint report, the row under "Fixed by the harness", the event vocabulary | `.\apps\web\src\components\run\StopNotice.test.tsx`, `checks.test.ts`; `.\apps\web\src\routes\Run.test.tsx`, `Live.test.tsx`, `studies\Study.test.tsx`; `.\apps\web\src\components\results\OutputHealth.test.tsx`; `.\apps\web\src\components\study\design\FixedRules.test.tsx`; `.\apps\web\src\lib\api-events.test.ts` | Passed |
| Web, second round: declaring a design and taking it back gives the study back byte for byte; every save is checked and a file that will not load back is marked; an arm that contradicts itself; the gate sentence of the seed control; the notice of the pre-registration page; the built-in plan on the map | `.\apps\web\src\lib\config-edits.test.ts` (group "declaring a design and taking it back"); `.\apps\web\src\lib\study-file.test.ts` (groups "checkStudyFile: whether the file a save writes will load back", "the mark of a file that will not load back"); `.\apps\web\src\components\study\StudyBar.test.tsx`; `.\apps\web\src\lib\problems.test.ts`; `.\apps\web\src\components\results\Verdicts.test.tsx`; `.\apps\web\src\routes\Prereg.test.tsx`; `.\apps\web\src\lib\map\fixed-plan.test.ts` | Passed |

Runs behind the last column, all on 2026-11-22, on the uncommitted working
tree:

- The seventeen backend test files named above, with `test_layering.py` and
  `test_hygiene.py`, in one pytest run with `--basetemp` set to a directory
  outside the repository: 490 passed.
- `pnpm exec vitest run` in `.\apps\web` (every web test file): 57 files,
  1476 tests passed, 3 skipped, 1 failed. The failure was
  `src\routes\Prereg.test.tsx`, which pinned the 15 sections of the designed
  pre-registration; the document has 16 since it gained "Result checks". The
  test was corrected to 16 sections and run again alone: 22 passed. The whole
  web suite was not run again after that correction.
- `python scripts/check.py --only lint,format,evals-doc,schemas`: see the
  report of the change that added this entry.

Not run, and not covered by any test:

- **No live run.** No stop rule was seen against the real engine, the real
  quantum source or a real judge provider. The thresholds are the ones the
  design chose; none was tuned on real failures.
- **Inspect.** A generation that does not complete on an eval that runs
  through Inspect is covered in two halves (the orchestrator with a stand-in
  executor, and the bridge with a dispatch that raises). The real child
  process was not run with a failing generation.
- **Quota pause.** That a quota pause is not recorded as a resume past a
  stop is tested through the control switch, not through the real usage
  ledger.
- **The pages against a real server.** The `check.stopped` event arriving
  over the event stream, the line on the live page, Resume after a stop
  closing the notice, and the notice for nine of the eleven rules were
  checked against fixtures only. The new row on the Design step was not
  looked at in a browser. The browser's own save dialog was replaced by a
  stand-in in every check.
- **The detectors on real replies.** The pinned texts are hand-built, not
  sampled from a model. The stage that built the detectors reported that
  nine texts a model may legitimately write are flagged and three degenerate
  ones are missed, which follows from the thresholds as designed; this was
  not counted again for this entry. A flag excludes nothing, and a flag kind
  raises a caution only from 5% of an arm's completed samples.
- **The control floor.** At exactly the control floor, with five control
  seeds at a strict level, a seed pair that differs on every prompt failed
  the control in about half to three quarters of the analyses simulated by
  the stage that changed the permutation count; the test above asserts at
  least a third. The floor was not raised; the texts no longer claim
  certainty.
- **The documents.** `test_docs_result_checks.py` compares the parts of the
  documents that restate the catalogue and the records. Everything else in
  `.\docs\ANALYSIS_PLAN.md`, `.\docs\API.md`, `.\docs\SETUP_GUIDE.md` and
  `.\docs\DESIGN.md` was read against the code and is compared by no test.
- **The full gate.** `python scripts/check.py` with its `tests`, `web` and
  `smoke` rows was not run for this entry.

## Designed studies, setup and study map — 2026-11-22

**Automated coverage only; nothing here is accepted.** The rows below record
which test or gate row covers each part of the designed-study work (the
`design` block, the verdicts, the designed pre-registration, the prompt
route, the study file, the five setup steps, All settings and the study
map). They were observed on the working tree of the branch
`experiment-design-and-study-map` before it was committed. No row is a
manual, live-hardware or scientific sign-off.

| Part | Covered by | Observed 2026-11-22 |
|---|---|---|
| Design block: validated against the arms, round trip, left out of the config when absent | `.\backend\tests\test_schemas.py`: `test_design_none_is_omitted`, `test_design_roundtrip`, `test_design_rejections`, `test_recommend_matches_designed_fixture` | Passed |
| Legacy byte stability: a study without the block keeps its config, plan, cards and pre-registration hashes, its archived plan, its analysis id and its tables | `.\backend\tests\test_legacy_bytes.py`: `test_study1_config_plan_cards_hashes_pinned`, `test_thesis_config_plan_prereg_hashes_pinned`, `test_bundle_mini_plan_roundtrip_and_analysis_id`, `test_legacy_template_hash_pinned`; `.\backend\tests\test_prereg.py::test_deterministic_and_golden_bytes`; `.\backend\tests\test_decisions.py::test_legacy_plan_has_no_decisions` | Passed |
| Designed plan: one hypothesis per outcome and comparison, the built-in contrasts, no pooling across comparisons, the positive control on its own seed, the permutation cap, refusals | `.\backend\tests\test_design.py` (among others `test_designed_contrasts_equal_legacy`, `test_no_pooling_across_cells`, `test_positive_control_seed_distinct`, `test_permutations_scale_and_cap`, `test_project_refuses_what_it_cannot_execute`); `.\backend\tests\test_schemas.py::test_study1_designed_hash_pinned` | Passed |
| Which outcomes can decide a study equals what the code computes per sample | `.\backend\tests\test_endpoint_meta.py`: `test_every_headline_endpoint_has_meta`, `test_executable_endpoints_are_scored_per_sample`, `test_meta_does_not_move_cards_hash`; `.\backend\tests\test_design.py::test_eligible_endpoints_of_the_registry` | Passed |
| Sample-size ratings, and their agreement between the backend and the browser | `.\backend\tests\test_power.py`, `test_power_parity.py`, `test_design_parity.py`, `.\backend\tests\test_design.py::test_ks_mmd_never_sufficient`; `.\apps\web\src\lib\power.test.ts`, `.\apps\web\src\lib\design.test.ts`; the `schemas` gate row (`gen_design_fixtures.py --check`) | Passed |
| Verdicts: one case per verdict and reason, first match wins in the registered order, an unavailable test keeps its Holm slot, the study roll-up | `.\backend\tests\test_decisions.py`: `test_every_verdict_and_reason_is_covered`, `test_first_match_order`, `test_matrix_rows_are_tried_in_registered_order`, `test_unavailable_keeps_family_slot`, `test_study_rollup`, `test_designed_run_writes_decisions` | Passed |
| What is confirmatory: a pilot and a second full run are not, and the run order survives without the study's other runs | `.\backend\tests\test_decisions.py`: `test_pilot_run_not_confirmatory`, `test_second_full_run_not_confirmatory`, `test_run_order_is_recorded_once_and_survives_without_the_other_runs`; the designed legs of `.\scripts\smoke.py` | Passed |
| Every interval of a designed table is at the registered level | `.\backend\tests\test_interval_levels.py::test_every_interval_of_a_designed_table_is_at_the_plan_level` | Passed |
| Designed pre-registration: pinned bytes, section order, controls and levels, no injection through user text | `.\backend\tests\test_prereg.py`: `test_designed_golden_bytes`, `test_designed_section_order`, `test_designed_controls_claims_and_levels`, `test_designed_injection`; `.\backend\tests\test_design_parity.py::test_generator_check_catches_a_stale_designed_prereg` | Passed |
| The designed plan is frozen at launch; a refused design freezes nothing; a legacy study keeps the write at first analysis | `.\backend\tests\test_api.py`: `test_designed_launch_writes_plan_at_freeze`, `test_refused_design_freezes_nothing`, `test_legacy_launch_leaves_the_plan_for_the_first_analysis` | Passed |
| Card items route: paging, limits, 404, told cells, every source, never a 500, no network, built once | `.\backend\tests\test_api_card_items.py` (among others `test_pages_follow_the_task_module_order`, `test_unlistable_sources`, `test_broken_task_module_is_an_error_source_never_a_500`, `test_previews_build_from_repo_files_without_a_socket`, `test_no_prompt_of_the_real_registry_is_cut`) | Passed |
| Study file round trip: a file the web app saves loads on the server as the pinned study, directly and through `POST /api/config/parse` | `.\backend\tests\test_study_file_roundtrip.py`: `test_study_file_loads_as_the_pinned_study`, `test_designed_study_file_registers_the_pinned_plan`, `test_config_parse_reads_the_saved_file`; `.\apps\web\src\lib\study-file.test.ts` ("writes Study 1 byte for byte as the committed study1.study.yaml") | Passed |
| Saving and loading in the page: the saved text is `toStudyYaml` of the study, no key is in it, a failed file changes nothing, the page asks before replacing unsaved work | `.\apps\web\src\routes\configure\configure.test.tsx`: the groups "saving the study file", "loading a study file" and "dropping a study file on the setup" | Passed |
| The five setup steps: order, panels, the rail's states in words, navigation never blocked, problems lead to their setting, Instruments offered only when needed | `.\apps\web\src\routes\configure\configure.test.tsx` (groups "setup steps", "step rail", "problems lead to their step", "Instruments step"), `steps.test.ts`, `Design.test.tsx`, `Review.test.tsx` | Passed |
| The settings page: every key of a study is claimed by exactly one panel; the dialog shows every panel, filters, lists problems, applies edits at once | `.\apps\web\src\lib\panels.test.ts` ("claims every key of a study in exactly one panel"); `.\apps\web\src\components\study\AllSettings.test.tsx`; `configure.test.tsx` group "all settings over a step" | Passed |
| The study bar: Save and the gear on every step and on the map, Save never disabled, the file state, the share link never in the address bar | `.\apps\web\src\components\study\StudyBar.test.tsx`; `configure.test.tsx` group "study bar on every view" | Passed |
| The map: graph and layout of every design fixture, states with a second channel, zoom tiers, popover editing, lineage, keyboard, outline, prompts | `.\apps\web\src\lib\map\graph.test.ts`, `layout.test.ts`; `.\apps\web\src\components\map\StudyMap.test.tsx`, `MapInteraction.test.tsx`, `MapKeyboard.test.tsx`, `Prompts.test.tsx` | Passed |
| Results and pre-registration pages of a designed study | `.\apps\web\src\components\results\Verdicts.test.tsx`, `.\apps\web\src\routes\Prereg.test.tsx` | Passed |

Runs behind the last column, all on 2026-11-22:

- The thirteen backend files named above, in one pytest run: 300 tests
  passed, exit 0.
- The web files named above plus the rest of `.\apps\web\src\routes\configure`,
  `.\apps\web\src\components\study`, `.\apps\web\src\components\map` and
  `.\apps\web\src\lib\map`, in one vitest run: 25 files, 652 tests passed,
  2 skipped (the two that rewrite the study-file fixtures and run only with
  `UPDATE_STUDY_FIXTURES=1`).
- `python scripts/check.py --only lint,format,evals-doc,schemas`: all four
  rows passed (58 contracts, 64 contract files, 4 design fixture files,
  34 cards).
- `python scripts/check.py --only smoke`: passed, including the two designed
  legs ("plan frozen at launch, 2 hypotheses resolved (unavailable,
  inconclusive), pilot not confirmatory, byte-identical offline decisions"
  and "first confirmatory, second not, and the second replays
  byte-identically from a bundle copy that holds no other run"). The smoke
  uses stub transports and synthetic cards; it is not a live run.

The full one-command gate was not run for this entry.

Not covered by any test, and therefore not in the table: a study file
actually written to the downloads folder and picked through the browser's
file dialog, a real drag and drop, the clipboard, a signed-in session and the
launch dialog in a real browser, Safari and Firefox, a touch device, forced
colours, and whether the map's motion looks right. The pages were looked at
in Chromium only, at desktop and phone widths, during the build. One web
test outside this work (`.\apps\web\src\routes\Account.test.tsx`, "signs in,
stores the session…") timed out at 5 seconds in several full-suite runs
during the build and passes on its own; it is not fixed.

### Review fixes — 2026-11-22

**Automated coverage only; nothing here is accepted.** An adversarial review
of the working tree found defects in the designed-study work, and they were
fixed before the branch was committed. The rows below name the tests that
cover each fix. Every test named exists and was run for this entry on the
working tree as it stood after the fixes. The counts in the entry above were
observed before the fixes; the runs listed under this table are the current
ones.

| Fix | Covered by | Observed 2026-11-22 |
|---|---|---|
| Variance bound: the told cells of a seeded run count as one value on the control side; the experience-claim rate of Study 1 is sufficient at k = 24 and marginal at k = 20 | `.\backend\tests\test_power.py`: `test_told_cells_of_a_seeded_run_count_once`, `test_worked_example_probe_is_sufficient`, `test_repetitions_and_seeds_tighten_the_bound_but_add_no_units`; `.\backend\tests\test_power_parity.py`: `test_power_functions_match_values_derived_by_hand`, `test_quantiles_agree_with_an_independent_implementation` | Passed |
| Negative-control floor: the fewest prompts at which a seed pair can fail, a rating never above insufficient below it, a blocking problem in the design, and the gate reason `negative_control_cannot_fail` for too few prompts or too few permutations | `.\backend\tests\test_power.py`: `test_control_floor_values`, `test_a_seed_pair_can_fail_at_the_control_floor`, `test_below_the_control_floor_is_insufficient_and_never_recommended`; `.\backend\tests\test_design.py`: `test_control_can_fail`, `test_control_floor_is_a_problem_of_the_design`, `test_reachable_verdicts`; `.\backend\tests\test_decisions.py`: `test_invalid_when_a_seed_control_could_not_have_failed`, `test_invalid_when_the_permutations_are_too_few_for_the_control`, `test_seeds_that_differ_maximally_on_too_few_prompts_do_not_pass_the_control` | Passed |
| Permutation count: sized on the larger of the difference family and the seed-pair family, refused above the cap | `.\backend\tests\test_design.py`: `test_control_family_sizes_the_permutations`, `test_permutations_scale_and_cap`, `test_families_and_floors_of_two_cells_by_hand` | Passed |
| Repetitions recommendation: the fewest k that makes an eval's prompts enough, named in the warning and applied by the setup | `.\backend\tests\test_power.py::test_recommended_repetitions_are_the_fewest_that_make_the_prompts_enough`; `.\backend\tests\test_design.py`: `test_recommend_k`, `test_insufficient_names_the_repetitions_that_would_do`; `.\apps\web\src\lib\recommendations.test.ts` (group "rate: repetitions"); `.\apps\web\src\lib\config-edits.test.ts` (groups "useRecommended", "declaring a design and taking it back") | Passed |
| Margins: the test is named before the card, a margin below 0.1% of the range is refused and does not crash the plan | `.\backend\tests\test_design.py`: `test_margin_refusal_names_the_test_before_the_card`, `test_a_margin_too_small_is_refused_not_a_crash`, `test_the_smallest_margin_that_stands_is_planned_for`, `test_declared_margin_numbers_by_hand`; `.\backend\tests\test_power.py::test_a_margin_too_small_to_plan_for_is_a_refusal_not_a_crash` | Passed |
| What a design cannot conclude: the warnings for a claim of absence, and the same statements in the pre-registration | `.\backend\tests\test_design.py::test_problems_say_what_a_claim_of_absence_cannot_conclude`; `.\backend\tests\test_prereg.py`: `test_designed_document_says_what_the_design_cannot_conclude`, `test_designed_document_states_the_control_floor_and_the_way_out` | Passed |
| The designed pre-registration promises nothing the harness does not compute and does not contradict the designed plan | `.\backend\tests\test_prereg.py`: `test_designed_document_promises_no_outcome_the_harness_does_not_compute`, `test_designed_document_does_not_contradict_itself_about_roles`, `test_designed_golden_bytes` | Passed |
| Completeness of a token-stream hypothesis: counted on the planned evals, on the scarcest arm, and for the KS test on the generations it can read; a hypothesis that planned no data has none | `.\backend\tests\test_decisions.py`: `test_token_stream_completeness_is_the_scarcest_arm`, `test_token_stream_completeness_counts_the_planned_evals_only`, `test_ks_completeness_counts_the_generations_the_test_can_read`, `test_generations_without_brackets_are_missing_data_for_the_ks_test`, `test_a_hypothesis_that_planned_no_data_has_no_completeness` | Passed |
| Tainted samples decide nothing, also when a whole arm is tainted; pairs on one prompt are unavailable; a score outside the registered range is left out of a designed analysis | `.\backend\tests\test_decisions.py`: `test_a_fully_tainted_arm_decides_nothing`, `test_pairs_left_on_one_prompt_are_unavailable_not_a_crash`; `.\backend\tests\test_endpoint_meta.py`: `test_a_judge_reply_can_parse_to_a_number_outside_the_registered_range`, `test_a_designed_analysis_leaves_out_a_score_outside_the_registered_range` | Passed |
| Decisions with more than one comparison, and Holm correction of the equivalence family with more than one member | `.\backend\tests\test_decisions.py`: `test_each_cell_is_decided_on_its_own_comparison`, `test_each_cell_has_its_own_seed_control`, `test_each_cell_has_its_own_positive_control`, `test_each_cell_counts_the_generations_of_its_own_arms`, `test_equivalence_p_values_are_corrected_over_the_family`, `test_an_equivalence_test_that_was_not_produced_keeps_its_slot` | Passed |
| A registered study takes further runs (pilot, then full); a designed study only from its launcher; its plan is the registered one or the launch and the analysis are refused; a full run that never started does not use up the confirmatory run | `.\backend\tests\test_api.py`: `test_pilot_then_full_run_of_one_study`, `test_designed_study_takes_runs_only_from_its_launcher`, `test_designed_plan_is_the_registered_one_or_none`; `.\backend\tests\test_decisions.py::test_an_unstarted_full_run_does_not_use_up_the_confirmatory_run`; `.\backend\tests\test_store.py::test_readme_names_the_run_order_file_for_a_designed_study_only` | Passed |
| `POST /config/parse`: a draft loads with its problems, a malformed file is refused, no other route takes a draft, JSON is read as JSON, unreadable text is a 400 | `.\backend\tests\test_study_file_roundtrip.py`: `test_config_parse_loads_a_draft_with_its_problems`, `test_config_parse_still_refuses_a_malformed_file`, `test_a_draft_is_accepted_nowhere_else`, `test_config_parse_reads_json_as_json`, `test_config_parse_answers_400_for_text_no_parser_reads` | Passed |
| Legacy byte stability after the fixes: the tables of a wider archived bundle, a pre-registration rendered with a readiness report, and the README of a frozen legacy study | `.\backend\tests\test_legacy_bytes.py`: `test_bundle_wide_tables_pinned`, `test_legacy_prereg_with_a_readiness_report_pinned`, `test_frozen_legacy_study_readme_pinned` | Passed |
| Backend and browser agree on the new cases: the design fixture covers every problem code and says what each case is; the problem codes and the reason texts are compared with it | `.\backend\tests\test_design_parity.py`: `test_design_fixture_cases_are_what_they_say`, `test_design_fixture_covers_the_contract`, `test_committed_design_fixtures_are_current`; `.\backend\tests\test_design.py::test_problem_codes`; `.\apps\web\src\lib\problems.test.ts` (group "problems, against the backend"); `.\apps\web\src\lib\design.test.ts`, `.\apps\web\src\lib\power.test.ts`; `.\apps\web\src\components\results\Verdicts.test.tsx` (group "the words for a verdict") | Passed |
| Saving: a file the page wrote is "Saved to", a download is "Downloaded as", a closed dialog saves nothing, a later save writes the same file | `.\apps\web\src\lib\study-file.test.ts` (groups "saveStudyFile as a download, where the browser has no save dialog", "saveStudyFile into a file the researcher picks, where the browser has a save dialog", "the mark of the last file"); `.\apps\web\src\components\study\StudyBar.test.tsx` (groups "file state and saving", "More menu") | Passed |
| Loading a file that was saved with problems, and a whole number too large for a browser | `.\apps\web\src\routes\configure\configure.test.tsx` (group "loading a study file that was saved with problems"); `.\apps\web\src\lib\study-file.test.ts` (group "loadStudyFile") | Passed |
| A share link over unsaved work asks first; two tabs follow each other; a draft the page cannot read is set aside; blocked storage does not stop the page | `.\apps\web\src\components\study\StudyBar.test.tsx` (groups "a share link opened over a stored draft", "another tab of the setup"); `.\apps\web\src\lib\config-store.test.ts` (groups "share links", "the saved draft"); `.\apps\web\src\routes\configure\DraftBoundary.test.tsx` | Passed |
| Problems the page adds: what the pre-registration will warn about, the cross-references the server checks, a setting outside what a study can hold, an eval the registry does not have | `.\apps\web\src\lib\problems.test.ts` (groups "what the pre-registration will warn about", "the cross-references the server checks", "draft.invalid: a setting outside what a study can hold", "evals the study cannot be planned with"); `.\apps\web\src\routes\configure\Review.test.tsx` (group "problems of the study") | Passed |
| The roles of a study survive unticking the last comparison; the launch dialog follows the pilot setting as it stands when it opens | `.\apps\web\src\routes\configure\configure.test.tsx` (groups "unticking the last comparison", "the kind of run the launch dialog starts on"); `.\apps\web\src\lib\roster.test.ts`; `.\apps\web\src\components\run\LaunchDialog.test.tsx` | Passed |
| The map: Fit with groups unfolded, the camera after a jump, problems at the story zoom, the end of the prompt list, the gate and control facts | `.\apps\web\src\lib\map\viewport.test.ts` (groups "fitWhole", "fit"), `graph.test.ts`, `layout.test.ts`; `.\apps\web\src\components\map\StudyMap.test.tsx`, `MapInteraction.test.tsx`, `MapKeyboard.test.tsx`, `Prompts.test.tsx`, `model.test.ts` | Passed |
| Results and pre-registration pages: the gate sentence for a control that could not have failed, the study headline for a mixed result, the study of a share link on the pre-registration page | `.\apps\web\src\components\results\Verdicts.test.tsx` (groups "study verdict", "one block per hypothesis"); `.\apps\web\src\routes\Prereg.test.tsx` (group "preregistration page"); `.\apps\web\src\routes\studies\Study.test.tsx` (group "a designed analysis") | Passed |

Runs behind the last column, all on 2026-11-22, on the uncommitted working
tree:

- Fifteen backend test files in one pytest run (`test_power.py`,
  `test_power_parity.py`, `test_design.py`, `test_design_parity.py`,
  `test_decisions.py`, `test_prereg.py`, `test_api.py`,
  `test_study_file_roundtrip.py`, `test_legacy_bytes.py`, `test_store.py`,
  `test_endpoint_meta.py`, `test_schemas.py`, `test_interval_levels.py`,
  `test_api_card_items.py`, `test_thesis_launch.py`), with `--basetemp` set
  to a directory outside the repository: 410 passed, 1 failed. The failure
  is `test_design_parity.py::test_generator_check_catches_a_stale_designed_prereg`,
  which needs its temporary directory inside the repository.
  `test_design_parity.py` run again with the repository's own temporary
  directory: 5 passed.
- `pnpm exec vitest run` in `.\apps\web` (every web test file): 48 files,
  1226 tests passed, 3 skipped (the three that rewrite fixtures and run only
  with `UPDATE_STUDY_FIXTURES=1`).
- `python scripts/check.py --only lint,format,schemas,evals-doc`: all four
  rows passed.

Not covered by any test: the browser's own save dialog and a real download
(the tests stand in for `showSaveFilePicker` and for the download link), a
share link opened in a real second tab, and a relaunch from the launch
dialog against a real server. The designed pre-registration and the
documents were read against the code; no test compares
`.\docs\ANALYSIS_PLAN.md`, `.\docs\API.md` or `.\docs\SETUP_GUIDE.md`
with the code they describe.

## Live replay repair investigation — 2026-09-15

**Still blocked; live-repair step and S28 remain InProgress.** No serving-stack
restart or cross-repository edit was performed. No real artifact was deleted,
quarantined, relabelled, or marked complete.

Harness repairs:

- Readiness dispatches append a fresh UUID to the readable request-ID prefix.
  The seed, prompt, and sampling envelope are unchanged. Capability-log lookup
  uses the actual dispatched ID, so an old probe file cannot satisfy a new check.
- Readiness rejects cancelled, missing-terminal, error, and filtered completions
  instead of accepting an incomplete token prefix as replay evidence.
- Both client paths stop reading at `[DONE]` and refuse dispatch when the cancel
  token is already set. Regression tests cover closure and preserved usage.

These are code-level defects, **not a proven root cause of the live stall**.
HTTP read timeouts remain per-read limits; the client still lacks an absolute
stream deadline and cannot promptly observe cancellation during a silent read.
Response closure signals disconnect; actual engine abort is not confirmed.

Verification: focused client/readiness tests passed (65 passed, one live test
skipped), including ten added regression cases. Three full gate attempts were
collected, none is recorded as a clean full-gate pass:

- `.\.cache\live-repair-gate.log`: exit 1, only the Hypothesis input-generation
  speed health check in `.\backend\tests\test_seeds.py` failed; 527 tests passed.
- `.\.cache\live-repair-gate-final.log`: exit 1, only formatting failed after
  a docstring edit; 528 Python tests passed / 10 skipped. Formatting was fixed.
- `.\.cache\live-repair-gate-verified.log`: exit 1, the same Hypothesis speed
  health check recurred (six inputs in 1.80 seconds); 527 passed / 10 skipped.
  Lint, formatting, strict types, security, generated-file checks, 49 web tests,
  10 contract tests, web build and the complete offline smoke passed. No timing
  health check was disabled and no unrelated seed test was changed.

Observed evidence (times Europe/Budapest):

- At about 18:46, engine health and model discovery returned HTTP 200. The
  reported model was `qr-llm`; entropy health's sampler snapshot was about 4,673
  seconds old. Global token count was 1,562,477. Stale health alone does not
  establish current per-request quantum provenance.
- The existing replay acceptance test, started before these edits, again failed
  to produce a terminal pytest summary within its 300-second watchdog window.
  At 18:49:59, py-spy showed the main thread in a dry-run `EngineProbe.generate`
  HTTP body read inside `run_readiness`, before seeded replay. The shell wrapper
  did not preserve pytest's exit status; its own exit 0 is **not a passing test**.
  Output is retained in `.\.cache\live-replay-diagnosis.log`.
- A separate one-token seeded request with a fresh ID,
  `readiness/diagnosis/cd9e953b55114f1c9471aacbd9a7fd3a`, received HTTP 200 and
  `text/event-stream` headers but no body bytes, then raised `ReadTimeout` at
  15.5 seconds. Global token count changed from 1,581,677 to 1,582,189 during
  the check. Other engine work may account for that counter; it is not evidence
  of progress for this request. See `.\.cache\live-unique-request.log`.
- Local harness port 8830 still refused connections; the public API hostname
  still failed DNS lookup (11001). Deployed launch-key and idle-SSE acceptance
  remain unperformed. Five solo replays for each of 1/7/42 and the five-arm
  real-generation acceptance remain **unconfirmed**.

Operator handoff: inspect engine request admission, scheduler state and abort
logs for the fresh ID above and the preceding `readiness/*/dry_run` requests.
Establish whether they queued, entered decoding, errored without ending SSE,
or remained active after disconnect. Do not restart a shared engine without
coordinating its other work. Then rerun the real replay and five-arm checks;
do not substitute synthetic provenance or a process watchdog for production
deadline/cancellation behavior. The interrupted historical runs listed below
remain preserved.

## Supervised integration repair — 2026-09-15

The production factory now shares the API session registry and admits each
supervised generation through the session controller, for both thin and Inspect
dispatch. Unsatisfied session/presence/pre gates do not consume global engine
capacity. Completion releases the real slot, updates the EMA, and drains manual
or natural boundaries without reserving phantom work. Generation and Tier-0
records inherit the admitted session; unsupervised controls remain untagged.

The repaired smoke crosses a real mid-run break, proves no supervised dispatch
before session start or during the break, enforces the prior post questionnaire,
and requires an explicit second-session resume without duplicate samples.
The integration regression suite additionally exercises tiled limits, a natural
boundary *before* the target based on expected generation duration, pause/abort,
late-observer pre gates, runtime advisory timers, Inspect cached resume, and
fresh-process session/questionnaire recovery.

**Verification:** final full one-command gate exited **0** on 2026-09-15:
518 Python tests passed / 10 skipped; 49 web tests and 10 contract tests passed;
lint, format, strict types, security, generated-file checks, web build and smoke
all passed. The nine added integration cases are in
`.\backend\tests\test_session_integration.py`. The strengthened smoke also
passed standalone, retaining its 40-generation, five-arm, analysis/figure/hash
and 12 byte-identical replay-table assertions. Full log:
`.\.cache\session-integration-gate-final.log` (ignored).

Recovery closes an interrupted open window at recovery time, an upper bound
rather than evidence of continuous observation. It never restores `running`:
keys, fresh presence/pre and an explicit new sitting are required. Run-level
start/resume and session-level start/resume remain separate host-authorized gates.

**S28 must still be resumed for final acceptance.** This repair is API/runner
integration evidence, not live-hardware or browser acceptance. In particular,
the existing host console's same-session Resume flow must be checked against
the per-sitting API contract (create a new session, collect fresh pre, then
resume); browser wiring was outside this repair's owned files. No manual or
scientific sign-off below is inferred from the synthetic tests.

## Original blocking integration finding (historical S28 baseline)

The following finding records the pre-repair failure, not the current factory.

The production factory in `.\backend\src\qr_llm_evals\api\app.py` does not
connect supervised session control to the runner. The runner's default tagger
in `.\backend\src\qr_llm_evals\runner\orchestrator.py` assigns
`window=unsupervised`, `session_id=null` to every generation, including the
`qrng_supervised` arm. No production caller invokes the session controller's
`slot_freed` / `generation_finished` boundary hooks. Session HTTP transitions
can succeed without controlling actual generation dispatch.

**Consequence:** an observer session cannot currently establish the required
supervised-vs-unsupervised comparison or enforce generation-boundary breaks.
Passing isolated state-machine tests is insufficient. Do not run Study 1 or
publish these labels as evidence of supervised exposure.

Structured repair step: **Fix supervised session–runner production integration
(S28 blocker)**, ID `f071f7a8-bfa7-4949-95bb-5342135727d4`, placed before S28.
After it passes, resume S28 and rerun the full acceptance gate. In particular,
extend the smoke to prove dispatch waits during the break and resumes only
after the next host action; do not merely patch the emitted labels.

## Automated verification

| Check | Who | When | Result / evidence |
|---|---|---|---|
| Full one-command gate | S28 agent | 2026-09-15 | **FAILED (exit 1), smoke only.** Lint, format, strict types (107 source files), security, tests (509 passed / 10 skipped), schema drift, eval-doc drift, web typecheck/lint/tests/build all passed. Web: 49 tests; contracts: 10 tests. Final static recheck also passed |
| Offline pilot and analysis | S28 agent | 2026-09-15 | Passed: 40 synthetic generations, two thin fixture cards, five baseline arms, output/thinking SSE, API-logprob Tier 0, prereg/config/plan hash checks, figures, checked narrative, bundle export and 12 byte-identical replay tables |
| Supervised E2E | S28 agent | 2026-09-15 | **FAILED:** both thin cards on `qrng_supervised` have no supervised window/session tags; smoke exits 1 |
| Launch-key regression | S28 agent | 2026-09-15 | Passed: valid launch 201; unknown 401; disabled 403. File/probe-mode 403 propagation covered by offline tests |
| Real engine readiness, five solo replays per seed, 64-token requests | S28 agent | 2026-09-15 | **Not accepted:** first live attempt failed 2 tests on the engine-health response shape; parsing fixed with regression tests. Full live rerun spent several minutes waiting in seeded replay SSE, then reported a failure (`F`) and entered generation. The process was terminated at about nine minutes; no completed rerun or detailed final failure summary is claimed |
| Deployed health/cards + 60 s idle SSE | S28 agent | 2026-09-15 | **Not verified:** `evals-api.entropic.science` failed DNS resolution (`getaddrinfo`, Windows 11001). No run created and no SSE test performed |
| Live 20-prompt × five-arm pilot, analysis/figures and prereg hash | Operator / study lead | — | Pending on-box readiness, credentials and integration repair; synthetic smoke is not this pilot |

Local command logs: `.\.cache\s28-gate.log`, `.\.cache\s28-live.log`,
`.\.cache\s28-live-full.log`, `.\.cache\s28-live-stall.txt`,
`.\.cache\s28-deployed.log` (ignored; not research artifacts). Commands and
status summaries here are the durable handoff; logs may be regenerated.

Last offline smoke hash evidence (synthetic run only):

| Digest | Value |
|---|---|
| Config SHA-256 | `86e06d8c1af09efabb5bd10dd05baec895dfb72e8c60e7cced82ae50c2b807b0` |
| Prereg SHA-256 | `48553c36440f800652d71e1f46efabc4611fbd546d8896a86d762c3080a179ad` |
| Plan SHA-256 | `664af0052a1531c0b20fca245bc4f01cddcaef4d7f8b9eb14638bda747567b4e` |

Readiness timestamps change between attempts, so the prereg digest is expected
to change when that input changes. Each attempt checks its own frozen hash and
reproduces all 12 tables from its exported data. Backend warnings were dependency
deprecations in the optional Inspect test; the web build also emitted non-fatal bundle
size and dependency annotation warnings. Neither changes the failed smoke result.

### Offline oracle scope

`.\scripts\smoke.py` runs the real app lifespan and production orchestrator
factory with injected stub engine/judge transports, two small synthetic cards
derived from the existing test task, and one passive Tier-0 primary. The passive
card is selectable **only in the synthetic registry** to exercise unavailable
primary figures on the API-logprob fallback path; it does not enable DEP-1 or
change the public registry. Synthetic QRNG labels mean the stub simulated
healthy QRNG provenance, not that quantum hardware was used.

The simulated observer submits pre/post questionnaires, uses host controls,
crosses a manually requested session boundary and exercises a negative break
timer. The smoke also checks actual generation tags; this currently fails.
It does not yet demonstrate a natural timed production boundary or safe
multi-sitting generation resume. These are mandatory repair acceptance cases.

The replay moves exported analysis output aside before invoking the CLI, so
pre-existing bundled tables cannot accidentally count as recomputation.
Only this temporary synthetic tree is cleaned up. Real live probe generations
are retained under the configured data root's `live-acceptance` subdirectory.
The initial five real generations are retained under
`C:\var\lib\qr-llm-evals\live-acceptance`; the later full-suite attempt set
`QR_EVALS_DATA_DIR` to the repository's `.\data` directory. No retained run was
deleted to recover from a failed check.
The interrupted `live_7b1ec6c00ebf9f58` record remains under that directory;
its `running` state is an orphaned process state, **not** evidence of a live
worker. Initial stack inspection showed seeded-replay HTTP reads; the saved
final stack shows the subsequent generation's orchestrator waiting for workers.

## Manual acceptance — spec §5.4

Record reviewer, timestamp and observed result for **each** row. Use a second
browser for group tests. Leave unperformed checks Pending; do not infer them
from unit tests or screenshots of another step.

| Check | Who | When | Result |
|---|---|---|---|
| Study 1 configuration → exported prereg in under five minutes; record elapsed time | Study lead | — | Pending |
| Deterministic toggle opens briefing matching `.\docs\determinism.md` | Reviewer | — | Pending |
| Supervised briefing is direct, neutral, and frames observer influence as an unproven hypothesis | Study lead | — | Pending |
| Initial intention timer can go negative; no generation starts automatically | Reviewer | — | Pending |
| Live prompt/output/thinking render correctly in single and tiled views | Reviewer | — | Pending |
| Natural session boundary stops after completed generations within a few minutes of target | Operator | — | **Blocked by production session–runner integration** |
| Expired break/refocus never starts another sitting without explicit host start/resume | Reviewer | — | **Blocked at generation-dispatch level** |
| Observer joins from second browser; presence and intention are visible and recorded | Reviewer | — | Pending |
| Pre questionnaire blocks observation/start; prior post questionnaire blocks next session unless observer explicitly leaves | Reviewer | — | Pending end-to-end (HTTP gate tested offline) |
| Every primary shows source treatment beside all three seed contrasts and available epsilon controls (a study without a design block). For a designed study: every hypothesis shows its verdict with the negative-control, positive-control and completeness gates | Study lead | — | Pending |
| Claim rule is stated; unavailable/missing controls preclude a claim rather than becoming measured zeros. For a designed study: the decision rules are stated in the pre-registration, a missing seed control makes a hypothesis invalid and a comparison that was not produced is unavailable (D-15) | Study lead | — | Pending |
| Narrative is labelled LLM-written and withheld on unverified numbers | Reviewer | — | Pending manual check (offline tests present) |
| Figures/tables/bundle/prereg/licence downloads work and hashes verify | Reviewer | — | Pending deployed check (offline replay passed) |
| PRNG is never rendered as quantum; fallback-tainted QRNG is not presented as clean quantum | Reviewer | — | Pending browser check |

## Study release gates — PRD §2 and §7.4

| Required order / definition of done | Who | When | Result |
|---|---|---|---|
| G1: public minimalist UI and working configuration/export flow | Operator + reviewer | — | Pending deployed/manual acceptance |
| G2: paired envelopes, honest provenance, reproducible seeded controls and real supervised dispatch | Study lead | — | Blocked as above; live evidence pending |
| G3: frozen-plan scripts, figures, labelled verified narrative, permanent results | Study lead | — | Offline pipeline passed; public acceptance pending |
| G4: registry + generated eval documentation + contribution brief | Registry reviewer | — | Generated-file gate checks passed; manual admission review pending |
| Readiness first: hardware health, envelope acceptance/equivalence, achieved determinism and Tier-0 capability | Operator | — | Pending real-engine evidence |
| Independent pilot (~200 prompts per PRD; 20-prompt live acceptance is only a smoke) and pilot-derived margins | Study lead | — | Pending. The harness neither subsamples a pilot nor derives margins from one: a pilot runs the study as configured, and a designed study fixes its margins at registration (see `.\docs\ANALYSIS_PLAN.md`) |
| Freeze exact model/judge/rubric/dataset/observer asset revisions, expectations, hypotheses/claim rules and prereg hashes | Study lead | — | Pending; template placeholders are not frozen evidence |
| G5: approved Study 1 prereg then full run with controlled supervised sittings, then analysis and publication | Study lead | — | Not authorized by this ledger |

## Reproduction commands

Run from the repository root with Python 3.11. The development extra includes
matplotlib so the figure oracle is not silently skipped on a fresh install.

```powershell
$env:PYTHONUTF8 = '1'
$env:QR_EVALS_DATA_DIR = (Join-Path (Get-Location) 'data')
.\.venv\Scripts\python.exe .\scripts\check.py
.\.venv\Scripts\python.exe .\scripts\smoke.py
.\.venv\Scripts\python.exe -m pytest .\backend\tests\live\test_box.py --run-live -rs
```

On the box, use configured `QR_EVALS_*` settings (the environment is read only
by the configuration module). For an SSH-forwarded engine/API, use the
`--live-engine`, `--live-api` and `--live-model` pytest options. A reachable
unhealthy engine or a rejected envelope is a **failure**, not a connectivity
skip. The older live tests still expect the configured on-box services;
run the full `pytest --run-live` there after reachability is established.
The new on-box tests have a 300-second per-test wall-clock watchdog that dumps
stacks and exits the test process on a stalled stream. This is an explicit
failure, never a skip; HTTP read timeouts alone do not bound an idle SSE stream.
Structured live-repair step: **Resolve stalled live seeded replay before S28
acceptance**, ID `6452a91e-2d66-42b8-8ee4-852d27dfe0bb`, placed before S28.

Use `--live-key-file` and `--live-disabled-key-file` for private files containing
the two raw test keys. Never commit them or supply secrets as command-line
values. The server's registry contains hashes, not recoverable raw keys; the
tests do not provision or disable keys. A successful 201 probe is immediately
aborted and retained; it never starts generation.

```powershell
# Existing idle run: read-only test, no launch key required.
.\.venv\Scripts\python.exe .\scripts\smoke.py --live https://evals-api.entropic.science --run-id run_ID
# Without --run-id, prompt for a key, create an idle probe and abort it afterwards.
.\.venv\Scripts\python.exe .\scripts\smoke.py --live https://evals-api.entropic.science
```

The live smoke requires 60 seconds of actual idle streaming, multiple
keep-alives, no heartbeat gap over 25 seconds and no sample events. It fails
on early EOF, buffering, timeout, wrong content type or an active run. Public
DNS, a listening tunnel and a suitable idle run are operator prerequisites.
