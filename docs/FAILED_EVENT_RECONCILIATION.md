# Failed-event reconciliation report

**Inspection date:** 2026-11-05
**Scope:** local Compose Redis key `ratelimit:outbox:failed` and matching RabbitMQ/worker state.
**Method:** read-only Redis reads, RabbitMQ queue listing, and source inspection. Event payloads and identifiers are intentionally omitted. No failed-list entry was changed or replayed.

## Findings

| Check | Result |
|---|---:|
| Failed-list entries | 28 |
| Valid JSON entries | 28; malformed 0 |
| Entries at retry limit | 28 / 28 (`publish_attempts=8`) |
| Event IDs | 28 present, 28 unique, 0 duplicate entries |
| Decision type | 26 accepted, 2 rejected |
| Request-history match | 28 / 28; each `request_id` matches its `event_id` |
| Processed idempotency marker | 28 / 28 present, none missing/expired |
| Marker TTL remaining | 598,552–602,191 seconds (about 6.9 days) |
| RabbitMQ ready/dead queue depth | 0 / 0; one consumer on the ready queue |
| Current outbox/inflight depth | 0 / 0 |
| Worker failure counter | 0 |
| Failed-list overflow cause | 0 indicated; all entries reached the eight-attempt limit |

At inspection, cumulative publisher counters were `events_published=4483`, `publisher_retries=227`, and `publish_failures=32`; the `outbox_overflow` metric was absent and reads as 0. These are lifetime aggregates and cannot be attributed to the 28 entries individually.

The events' request timestamps span 2026-11-04 18:53:58–19:14:55 UTC. The failed-event records contain the original request/event fields and `publish_attempts`, but no `last_error`, publisher exception, or failure timestamp. Their `reason` field is the rate-limit decision reason, not the publisher failure cause. Therefore the precise exception for each individual entry cannot be recovered from Redis.

## Failure category and likely cause

All 28 entries are **retry-exhausted**, rather than entries moved because the retry outbox was full. The prior relay raised `RuntimeError` whenever `BlockingChannel.basic_publish()` returned a falsey value. With Pika 1.3.2 publisher confirms, a successful ACK returns `None`; broker NACKs and mandatory unroutable messages raise exceptions. Thus a positively acknowledged publish could be treated as a failure and retried until the event reached eight attempts. The old relay logs observed during investigation showed these `RuntimeError` retries. The current relay was corrected to rely on confirm-mode exceptions and publish with `mandatory=True`.

This is the high-confidence explanation for the burst, but the entries themselves do not retain per-attempt causes, and historical logs from the replaced container are unavailable. It is not possible to prove that every one of the eight attempts for every entry received an ACK. The eight-attempt terminal state is certain; a true transient broker failure for a particular attempt cannot be ruled out.

## Delivery, duplicates, and replay effects

- There are no duplicate IDs *within the 28-entry failed list*. Every event ID has a matching request-history row and a live `ratelimit:event:processed:<event_id>` marker, which indicates each event reached worker processing at least once. RabbitMQ currently has no ready or dead-letter messages.
- The worker uses `SET marker NX EX 604800`; an event replay while its marker exists returns before changing worker counters. All 28 markers are currently live.
- Request history and API decision counters are written by the original Flask request path, before enqueueing. Replaying an event through the publisher/worker does not re-run that path and therefore does not append request history or increment those API decision counters.
- Worker processing increments only `ratelimit:worker:processed` and `ratelimit:worker:by_decision`, after writing the marker. A replay now should be deduplicated by the live markers, but it is unnecessary and would not repair a possible marker-before-counter crash. Once a marker expires, replaying can increment worker counters again. The marker and counter updates are not atomic, so marker presence is strong evidence of processing but not proof that both counter writes completed.

## Reconciliation decision

**No event is recommended for retry now.** All 28 have active idempotency markers and matching request-history records; the relay backlog and RabbitMQ queues are empty. Retain the failed entries unchanged for manual review. Do not replay them as a batch. If later investigation requires a retry, first preserve a protected copy of the failed list and review each event while its marker is live; separately decide how worker aggregates should be reconciled. Do not retry after the seven-day markers expire without an explicit duplicate-accounting plan.

## Regression coverage added

Worker tests now assert that retry exhaustion preserves the original event ID/request metadata and is distinct from outbox overflow, that a live idempotency marker suppresses duplicate worker effects with the seven-day TTL, and that replay after marker expiry applies those effects again. The Pika 1.3.2 `None`-on-ACK regression test also remains in place. These tests use fake Redis and do not access or modify the 28 stored entries.
