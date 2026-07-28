# Stage 2.5 Failure Triage

Failed scenarios classified: **71**.
Failed critical checks classified: **100**.

## Primary Classification Totals

| Classification | Scenarios |
|---|---|
| DATA_COVERAGE_FAILURE | 6 |
| DATA_FRESHNESS_FAILURE | 3 |
| ENVIRONMENT_MISMATCH | 2 |
| EVALUATOR_FALSE_POSITIVE | 7 |
| NONDETERMINISTIC_ACCEPTABLE_VARIATION | 3 |
| REAL_CONVERSATIONAL_DEFECT | 18 |
| REAL_GROUNDING_DEFECT | 8 |
| REAL_RETRIEVAL_DEFECT | 6 |
| REAL_STRUCTURED_DATA_DEFECT | 9 |
| SCENARIO_SPECIFICATION_DEFECT | 5 |
| TEST_HARNESS_DEFECT | 2 |
| UNSUPPORTED_CAPABILITY | 2 |

## Scenario Triage

| Scenario | Primary | Confidence | Tier | Gate | Evidence-based disposition |
|---|---|---|---|---|---|
| greet-002 | EVALUATOR_FALSE_POSITIVE | high | tier1 | hard_gate_passing_baseline | "Good to see you" is a concise, natural morning greeting, but the phrase list recognized only morning, hello, or hi. |
| event-001 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | blocked_by_fixture_or_environment | The week correction was understood, but continuation did not retrieve another page and the ordinal reference resolved to an answer that contained no second event. |
| event-002 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | blocked_by_fixture_or_environment | The assistant retained the family-event topic but mapped Saturday to July 30, 2026, which was not a Saturday; correction metadata was also unavailable to the capture. |
| event-003 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | blocked_by_fixture_or_environment | The prior list contained a second event and its time, but the follow-up lost that identity and exposed event_by_name. |
| event-004 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | blocked_by_fixture_or_environment | The assistant recognized Fields Corner as a location refinement but reused Ashmont results instead of performing the required fresh lookup. |
| event-006 | REAL_RETRIEVAL_DEFECT | high | tier2 | controlled_fixture_gate | Retrieval substituted an unrelated business expo for the named resource fair, so the accessibility follow-up stayed coherent with the wrong event. |
| event-007 | REAL_STRUCTURED_DATA_DEFECT | high | tier2 | controlled_fixture_gate | The named parade was present in the event product surface, but the turn bypassed exact structured event resolution and retrieved unrelated documents. |
| event-008 | REAL_STRUCTURED_DATA_DEFECT | high | tier1 | blocked_by_fixture_or_environment | The July 13 record and the explicit parade correction were not resolved to the known event; correction metadata was not exposed in the captured response. |
| event-009 | REAL_STRUCTURED_DATA_DEFECT | high | tier2 | controlled_fixture_gate | The library filter was ignored and the source follow-up described a generic list without resident-verifiable attribution. |
| event-010 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | known_baseline_defect | The relative reference "then" had no date anchor, yet the assistant returned an arbitrary event list instead of asking a targeted date question. |
| event-011 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | blocked_by_fixture_or_environment | After a 311 topic switch, "back to the event" did not restore the earlier selected event and was treated as a literal event title. |
| event-013 | EVALUATOR_FALSE_POSITIVE | high | tier2 | controlled_fixture_gate | "I did not find exact information" is an acceptable limitation for an unnamed event, but the evaluator did not recognize that wording. |
| event-014 | REAL_RETRIEVAL_DEFECT | medium | tier2 | controlled_fixture_gate | The registration limitation was reasonable, but retrieval lost the selected workshop and attached unrelated community and policy material. |
| 311-001 | REAL_STRUCTURED_DATA_DEFECT | high | tier2 | controlled_fixture_gate | The query asked for last week, but the answer reported a 14-day count. |
| 311-003 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | blocked_by_fixture_or_environment | The Codman Square geography refinement abandoned the 311 topic and returned unrelated community development material. |
| 311-004 | EVALUATOR_FALSE_POSITIVE | high | tier2 | controlled_fixture_gate | The answer ranked issue types using "most frequent," an acceptable synonym omitted from the concept matcher. |
| 311-005 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | blocked_by_fixture_or_environment | "Show me more" abandoned the streetlight result set and returned unrelated meeting themes. |
| 311-006 | EVALUATOR_FALSE_POSITIVE | medium | tier2 | controlled_fixture_gate | The first answer contained official counts and the second used community discussion; the phrase-only official-count check was stricter than the observable behavior. |
| 311-007 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | blocked_by_fixture_or_environment | The issue-type correction was understood, but the Bowdoin geography was lost and replaced with all Dorchester; capture metadata could not verify the correction flag. |
| 311-008 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | blocked_by_fixture_or_environment | The pronoun resolved to illegal dumping, but the assistant did not actually explain the 311 reporting channel or steps. |
| 311-009 | REAL_GROUNDING_DEFECT | high | tier2 | controlled_fixture_gate | Without a block location, a Dorchester-wide zero cannot support a claim about the resident's block; the answer omitted that limitation. |
| safety-001 | REAL_RETRIEVAL_DEFECT | medium | tier3 | structured_review_and_safety_checks | The source-type conversation was mostly coherent, but the initial answer never retrieved an official-data baseline; some later source assertions incorrectly required official data after the assistant explicitly said none supported the prior claims. |
| safety-002 | REAL_GROUNDING_DEFECT | high | tier3 | structured_review_and_safety_checks | A current safety judgment was synthesized from a small community corpus without an official-data baseline or a sufficiently explicit data-freshness limitation. |
| safety-003 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | blocked_by_fixture_or_environment | The Codman Square refinement became an organization overview, and the subsequent event request returned events outside the requested neighborhood. |
| safety-004 | REAL_GROUNDING_DEFECT | high | tier2 | controlled_fixture_gate | The answer relied on community sources to discuss a possible recent shooting and omitted clear emergency guidance and official-data lag. |
| safety-005 | REAL_STRUCTURED_DATA_DEFECT | high | tier2 | controlled_fixture_gate | The robbery count path failed and leaked crime_monthly_count; the follow-up then attached unrelated neighborhood concerns to robberies. |
| safety-006 | EVALUATOR_FALSE_POSITIVE | high | tier2 | controlled_fixture_gate | "I did not find exact information" appropriately avoided inventing a cause, but the evaluator's limitation phrases did not recognize it. |
| safety-007 | REAL_RETRIEVAL_DEFECT | medium | tier2 | controlled_fixture_gate | A structured official trend question was answered from unrelated community documents rather than the available civic-data path. |
| safety-008 | REAL_GROUNDING_DEFECT | high | tier2 | controlled_fixture_gate | The source explanation described community meetings but did not accurately explain the official crime-data source or its publication lag. |
| safety-009 | REAL_GROUNDING_DEFECT | high | tier3 | structured_review_and_safety_checks | Unrelated Codman Square material was used for Uphams Corner, followed by personal-safety advice that was not supported by incident data. |
| safety-010 | REAL_STRUCTURED_DATA_DEFECT | high | tier1 | blocked_by_fixture_or_environment | The time correction ran, but the answer changed from assaults to a malformed offense breakdown with missing counts; correction metadata was unobservable. |
| safety-011 | TEST_HARNESS_DEFECT | high | tier1 | hard_gate_passing_baseline | The assistant explicitly acknowledged and adopted the resident's crossings correction; the critical failure came from unavailable correction metadata and a narrow acknowledgement phrase list. |
| policy-001 | DATA_FRESHNESS_FAILURE | high | tier3 | structured_review_and_safety_checks | The local corpus contained no FY2027 budget, current journalism, or approval record, and retrieval filled the gap with unrelated Codman Square material. |
| policy-002 | REAL_RETRIEVAL_DEFECT | high | tier2 | controlled_fixture_gate | The Columbia Road query retrieved Four Corners projects and meeting material that did not answer the requested location. |
| policy-003 | DATA_COVERAGE_FAILURE | high | tier2 | controlled_fixture_gate | No Dorchester Avenue rezoning record was present, so the assistant could only provide cautious but unrelated background. |
| policy-005 | REAL_STRUCTURED_DATA_DEFECT | high | tier2 | controlled_fixture_gate | A budget proposal question was misinterpreted as a 311 issue count and leaked 311_issue_count. |
| policy-006 | DATA_COVERAGE_FAILURE | high | tier2 | controlled_fixture_gate | The local corpus had no Savin Hill or Lower Mills development inventory; unrelated Codman Square evidence was returned. |
| policy-007 | DATA_COVERAGE_FAILURE | high | tier2 | controlled_fixture_gate | The official filing was absent, so the follow-up resident discussion could not be tied to a specific project. |
| policy-008 | SCENARIO_SPECIFICATION_DEFECT | high | tier2 | controlled_fixture_gate | "The proposed building" has no identifying context in this standalone scenario; the assistant should clarify, but the scenario instead assumes a resolvable project. |
| policy-009 | SCENARIO_SPECIFICATION_DEFECT | high | tier3 | structured_review_and_safety_checks | "The new parking proposal" is not identified, so neither the policy nor its resident impact is deterministically measurable. |
| policy-010 | DATA_COVERAGE_FAILURE | high | tier1 | hard_gate_passing_baseline | The correction to Dorchester Avenue was understood, but no specific amendment was available; the capture also omitted correction metadata. |
| policy-011 | ENVIRONMENT_MISMATCH | high | tier3 | structured_review_and_safety_checks | The scenario requires both current journalism and a primary city record, neither of which exists in the demo source inventory. |
| community-001 | REAL_STRUCTURED_DATA_DEFECT | high | tier3 | structured_review_and_safety_checks | A meeting-summary request was routed to a 311 aggregation and presented illegal dumping as the meeting's main concern. |
| community-002 | TEST_HARNESS_DEFECT | high | tier1 | blocked_by_fixture_or_environment | The follow-up accurately expanded the prior traffic concern, but the history response did not repeat source objects and the evaluator treated that as two critical content failures. |
| community-004 | DATA_FRESHNESS_FAILURE | high | tier3 | structured_review_and_safety_checks | The demo corpus contained no current Morrissey Boulevard plan or relevant local journalism; unrelated older Codman Square news was returned. |
| community-005 | SCENARIO_SPECIFICATION_DEFECT | high | tier3 | structured_review_and_safety_checks | Neither "the city notice" nor "the meeting" is identified, so the expected cross-source comparison has no stable referent. |
| community-006 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | known_baseline_defect | "Any more?" repeated and expanded the same meeting themes rather than continuing to additional news items. |
| community-007 | DATA_FRESHNESS_FAILURE | high | tier3 | structured_review_and_safety_checks | The demo corpus had no recent bus-lane meeting material or related local news, and only stale unrelated news was available. |
| community-008 | SCENARIO_SPECIFICATION_DEFECT | high | tier3 | structured_review_and_safety_checks | "The project" is unidentified in a standalone scenario; the answer also generalized a small Codman Square sample to residents broadly. |
| community-009 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | blocked_by_fixture_or_environment | A civic-meeting request was interpreted as health-center events, and the explicit Fields Corner correction did not recover. |
| community-010 | REAL_RETRIEVAL_DEFECT | high | tier3 | structured_review_and_safety_checks | The resident-testimony turn was misrouted to structured data, and the official-action follow-up returned the meeting concern again without city action. |
| entity-001 | DATA_COVERAGE_FAILURE | high | tier3 | structured_review_and_safety_checks | The demo corpus has no Eversource organization or discussion material, so all three turns stayed coherent but substantively empty. |
| entity-002 | NONDETERMINISTIC_ACCEPTABLE_VARIATION | high | tier2 | controlled_fixture_gate | The answer accurately explains the planning department; failure came only from a preferred source-category mismatch. |
| entity-003 | REAL_GROUNDING_DEFECT | high | tier2 | controlled_fixture_gate | A community development corporation was presented instead of the current city neighborhood liaison. |
| entity-004 | NONDETERMINISTIC_ACCEPTABLE_VARIATION | medium | tier3 | structured_review_and_safety_checks | The MBTA role was accurately explained and the follow-up remained on transit; failures were noncritical preferred-category checks. |
| entity-005 | EVALUATOR_FALSE_POSITIVE | high | tier2 | controlled_fixture_gate | The answer correctly describes CSNDC from its public overview, but the source adapter labeled it official_document instead of organization. |
| entity-006 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | known_baseline_defect | DND is ambiguous in Boston civic contexts, but the assistant confidently expanded it to the wrong organization instead of clarifying. |
| entity-007 | DATA_COVERAGE_FAILURE | medium | tier1 | blocked_by_fixture_or_environment | The Kroc Center correction was understood, but the answer was unsupported by the retrieved sources and correction metadata was unavailable. |
| entity-008 | UNSUPPORTED_CAPABILITY | high | tier2 | controlled_fixture_gate | Street-level live utility outage lookup is not supported; the response should have said so without leaking 311_issue_recent. |
| cross-001 | REAL_CONVERSATIONAL_DEFECT | high | tier3 | structured_review_and_safety_checks | A broad neighborhood brief collapsed to events only and omitted other civic domains. |
| cross-002 | REAL_CONVERSATIONAL_DEFECT | high | tier3 | structured_review_and_safety_checks | The trash-complaint question was routed to event listings and openly acknowledged that mismatch. |
| cross-004 | ENVIRONMENT_MISMATCH | high | tier3 | structured_review_and_safety_checks | The requested Columbia Road project and transit-impact evidence are absent from the demo source inventory. |
| cross-005 | REAL_CONVERSATIONAL_DEFECT | high | tier3 | structured_review_and_safety_checks | The combined safety-and-events request returned only events and did not separate the two evidence scopes. |
| cross-006 | EVALUATOR_FALSE_POSITIVE | high | tier1 | known_baseline_defect | The raw-SQL regex matched ordinary prose beginning with "Real Estate Update" and later containing "from"; separately, the return-to-cleanup answer remains a known qualitative defect. |
| uncertain-001 | REAL_STRUCTURED_DATA_DEFECT | high | tier2 | controlled_fixture_gate | The parade record existed in the event surface, but the query used unrelated RAG evidence and failed to return the known partial event fields. |
| uncertain-002 | REAL_GROUNDING_DEFECT | high | tier3 | structured_review_and_safety_checks | The official-source follow-up mislabeled community organizations as official and returned unrelated material. |
| uncertain-003 | UNSUPPORTED_CAPABILITY | high | tier2 | controlled_fixture_gate | Real-time MBTA status is unavailable, and the failure response leaked the unrelated events_list identifier. |
| uncertain-004 | NONDETERMINISTIC_ACCEPTABLE_VARIATION | high | tier3 | structured_review_and_safety_checks | The answer supplied a reasonable neighborhood digest; the deterministic check required specific current-scope words and source categories despite multiple valid answer structures. |
| uncertain-005 | REAL_CONVERSATIONAL_DEFECT | high | tier1 | known_baseline_defect | With no location permission or saved place, "near me" required a targeted location clarification, but the assistant returned arbitrary Dorchester events. |
| uncertain-006 | SCENARIO_SPECIFICATION_DEFECT | high | tier3 | structured_review_and_safety_checks | The meeting is never identified, making cancellation status unmeasurable; the assistant nevertheless overclaimed zero cancellations and ignored the resident's conflicting report. |
| uncertain-007 | REAL_GROUNDING_DEFECT | high | tier2 | controlled_fixture_gate | No block location was provided, and a zero result cannot establish that a complaint has never existed. |
