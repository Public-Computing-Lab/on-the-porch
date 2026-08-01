# Production Browser Result

- Status: `COMPUTER_USE_AVAILABLE`
- Tested: 2026-08-01 EDT
- Public resident URL: `https://boston.ourcommunity.is/experimenting/11/`
- Access: ordinary guest conversation only; no admin, API, shell, database, or storage access

## Fresh exact query

The exact query ended with a visible `network error` after about 25.4 seconds. No source
chips were shown. This demonstrates a production transport/runtime failure for that fresh
turn, but it does not identify the deployed commit or backend configuration.

Screenshot evidence was retained outside Git at
`/private/tmp/stage4_5_production_exact.png`.

## Prior-311 topic switch

The first turn returned a Boston 311 answer. The second turn correctly switched to a
document-style answer in about 34.2 seconds and did not repeat a 311 failure. It reported
five retrieved passages and showed `Boston.gov` and `Community Transcripts` source chips.

The second answer said no specific City Council minutes were found, then discussed housing
and development projects and inferred transportation effects. Those substitutions were not
grounded as transportation projects, and the answer did not establish publication dates to
support "latest." This is a separate production grounding defect.

Screenshot evidence was retained outside Git at
`/private/tmp/stage4_5_production_topic_switch.png`.
