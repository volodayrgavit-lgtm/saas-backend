# LAB51 — FINAL BILLING ARCHITECTURE
## Robokassa + Billing + SQLAlchemy + Transaction Assembler + gRPC + Outbox + Priority Worker Pools + PostgreSQL LISTEN/NOTIFY + Sync/Share
### Финальное техническое ТЗ для Сани и Артёма
### Версия 4.0 — 17.08.2026

---

## 0. Цель

Зафиксировать финальную архитектуру Billing LAB51 с учетом последних уточнений Сани:

- Robokassa принимает оплату;
- Billing — серверная точка истины по заказу/платежу;
- внутренние изменения подписки и ресурсов идут через gRPC;
- SQLAlchemy используется для локальных ACID-транзакций;
- сложные изменения собираются через `TransactionAssembler`;
- зависимости/FK описываются через `DependencyRegistry`;
- изменения маршрутизируются через durable очередь с приоритетами и несколькими worker pools;
- PostgreSQL `LISTEN/NOTIFY` используется для быстрого сигнала об обновлении, но не заменяет durable outbox;
- массовая синхронизация компонентов строится через domain events + Sync/Share REST;
- сайт, Telegram, расширение и CRM не являются источником истины по оплате/подписке.

---

## 1. Финальная схема

```text
Robokassa
    ↓ ResultURL
Billing Service
    ↓ validate signature / amount / InvId
SQLAlchemy UnitOfWork
    ├─ payment/order update
    ├─ build UpdatePlan
    └─ insert transactional outbox
    ↓ COMMIT
PostgreSQL NOTIFY
    ↓
Priority Dispatcher
    ├─ fast_pool
    ├─ transaction_pool
    ├─ distributed_pool
    └─ bulk_pool
            ↓ gRPC
      Core/User Service
            ↓ local transaction
      subscription / entitlements / resources
            ↓ outbox + NOTIFY
      Sync/Share Dispatcher
            ↓
Website / Telegram / Extension / CRM
```

Ключевой принцип:

```text
LOCAL CONSISTENCY       = SQLAlchemy transaction
DURABLE HANDOFF         = transactional outbox
LOW-LATENCY WAKE-UP     = LISTEN/NOTIFY
EXECUTION CONTROL       = priority queue + worker pools
CROSS-SERVICE COMMANDS  = gRPC
MUTATION PLANNING       = TransactionAssembler + DependencyRegistry
GLOBAL STATE SYNC       = events + Sync/Share REST
```

---

## 2. Ответственность

### Саня

Отвечает за:

- Billing backend;
- SQLAlchemy models/migrations;
- Robokassa adapter;
- ResultURL;
- order/payment/fiscal state;
- pricing/quote;
- `TransactionAssembler`;
- `DependencyRegistry`;
- Unit of Work;
- transactional outbox;
- priority dispatcher;
- worker pools;
- LISTEN/NOTIFY;
- protobuf/gRPC;
- Core-side gRPC handlers;
- retries/idempotency;
- orchestration state;
- Sync/Share backend;
- reconciliation;
- logs/metrics/tests.

### Артём

Отвечает за:

- register/login UI;
- checkout UI;
- quote/order API integration;
- redirect на Robokassa;
- success/fail UI;
- polling статуса;
- Telegram → web checkout;
- Extension → web checkout;
- CRM read-only payment/subscription view;
- отображение sync/activation states.

### Максим

Отвечает за:

- Robokassa owner/admin;
- production тарифы;
- юридические/фискальные параметры;
- выдачу минимальных доступов;
- финальный production approval;
- контролируемый live payment.

---

## 3. Запреты

```text
NO frontend trusted amount
NO Robokassa secrets in frontend
NO direct frontend DB writes
NO SuccessURL = paid
NO raw Robokassa callback → users table
NO Billing writes directly to foreign service DB
NO duplicated payment logic in site/TG/extension
NO non-idempotent payment processing
NO LISTEN/NOTIFY as the only queue
NO cross-service SQL FK if services own separate DBs
```

---

## 4. Billing checkout

```text
Website / Telegram / Extension
             ↓
        LAB51 Checkout
             ↓
      POST /billing/quote
             ↓
         Billing Core
             ↓
      server-side pricing
             ↓
      POST /billing/order
             ↓
      local order + InvId
             ↓
      Robokassa invoice
             ↓
        payment_url
```

Frontend передает `plan_id/config`, но не является источником суммы.

---

## 5. Основные сущности

```text
products
plans
prices
quotes
orders
order_items
payment_attempts
payment_events
fiscal_documents
billing_outbox
billing_sync_transactions
processed_events / inbox
```

---

## 6. Create Order

```http
POST /v1/billing/orders
Authorization: Bearer ...
Idempotency-Key: <uuid>
```

Вход:

```json
{
  "quote_id": "...",
  "source": "website"
}
```

Порядок:

```text
1. auth + user_id
2. validate quote/TTL
3. validate plan
4. pricing snapshot
5. create order UUID
6. create unique InvId
7. create order_items/fiscal snapshot
8. save local order
9. create Robokassa invoice
10. save provider metadata
11. return payment_url
```

Local order должен существовать до redirect в Robokassa.

---

## 7. ResultURL

Robokassa callback принимает только Billing.

Порядок:

```text
1. parse callback
2. find order by InvId
3. lock order/payment
4. validate signature
5. validate OutSum against stored amount
6. validate expected state
7. deduplicate callback/event
8. update payment/order
9. prepare UpdatePlan
10. insert outbox event
11. COMMIT
12. respond OK{InvId}
```

Никакого прямого `callback → UPDATE users`.

---

## 8. Статусы разделяются

Хранить отдельно:

```text
payment_status
fiscal_status
subscription_status
resource_sync_status
notification_status
```

Пример:

```text
payment_status       = PAID
fiscal_status        = PENDING
subscription_status  = ACTIVE
resource_sync_status = PROCESSING
```

Подтвержденный payment по умолчанию не должен ждать завершения фискализации для выдачи доступа. Фискализация имеет свой retry/alert контур.

---

## 9. TransactionAssembler

Нужен отдельный application-компонент:

```text
BillingTransactionAssembler
```

Его задача:

```text
order/payment
    ↓
user_id
    ↓
current plan/business rules
    ↓
dependency graph
    ↓
UpdatePlan
```

Пример `UpdatePlan`:

```json
{
  "event_id": "uuid",
  "correlation_id": "uuid",
  "order_id": "uuid",
  "user_id": "uuid",
  "operation": "ACTIVATE_OR_EXTEND",
  "plan_id": "advanced",
  "subscription": {
    "duration_months": 1
  },
  "entitlements": [
    {"name": "monitoring", "operation": "SET", "value": true}
  ],
  "resources": [
    {"resource": "search_limits", "operation": "RECALCULATE"}
  ]
}
```

UpdatePlan — контракт изменения, а не набор чужих ORM-моделей.

---

## 10. Общие ORM-модели или контракты

Если Billing/Core/Resources — реально отдельные сервисы с разными DB, Billing НЕ импортирует их SQLAlchemy-модели.

Правильно:

```text
Billing
→ protobuf/domain contract
→ gRPC
→ owning service
→ own ORM
```

Если сейчас фактически один PostgreSQL + один backend + модули, допустим общий persistence registry и одна ACID transaction. Тогда это сознательно modular monolith.

---

## 11. Обязательное решение до реализации

Саня фиксирует один из вариантов:

### A. Одна PostgreSQL DB

```text
SQLAlchemy UnitOfWork
+ DependencyRegistry
+ real FK
+ one ACID transaction
```

### B. Разные DB по сервисам

```text
local transaction
+ transactional outbox
+ queue
+ gRPC
+ idempotent consumers
+ saga/process manager
```

Нельзя пытаться растянуть одну SQL transaction на несколько независимых DB через gRPC.

---

## 12. Transactional Outbox

Payment update и событие на downstream должны быть одной локальной transaction.

Плохо:

```text
COMMIT payment
process died
queue message not sent
```

Правильно:

```text
BEGIN
payment = PAID
order = PAID
INSERT billing_outbox(...)
COMMIT
```

---

## 13. billing_outbox

Пример полей:

```text
id
event_id UNIQUE
correlation_id
event_type
aggregate_type
aggregate_id
user_id
order_id
priority
mutation_class
payload_json
status
attempt_count
next_attempt_at
claimed_at
lease_until
worker_id
created_at
published_at
completed_at
last_error
```

Статусы:

```text
PENDING
CLAIMED
PROCESSING
COMPLETED
RETRY
DEAD
```

---

## 14. LISTEN/NOTIFY — идея Сани

Использовать `LISTEN/NOTIFY` для низкой задержки при обновлении подписок и появлении новых jobs.

Но:

> `LISTEN/NOTIFY` — сигнал о том, что работа появилась, а не durable очередь.

Правильный flow:

```text
DB transaction
↓
outbox row committed
↓
NOTIFY billing_outbox_changed
↓
dispatcher wakes immediately
↓
dispatcher reads durable rows
↓
assigns worker pool
```

Если listener был offline, ничего страшного: после reconnect он повторно сканирует durable outbox.

---

## 15. Каналы LISTEN/NOTIFY

Не плодить сотни каналов.

Достаточно, например:

```text
billing_outbox_changed
core_events
resource_sync_changed
```

Payload небольшой:

```json
{
  "event_id": "uuid",
  "priority": 100,
  "mutation_class": "DISTRIBUTED_UPDATE"
}
```

Полные данные worker читает из БД/UpdatePlan.

---

## 16. Multi-pool priority queue

Идея Сани фиксируется как:

```text
Mutation Classification
+ Priority Dispatcher
+ Multiple Worker Pools
```

Нужно разделять легкие одиночные изменения и сложные созависимые транзакции.

---

## 17. Классы изменений

### CLASS 0 — READ / INVALIDATION

Примеры:

```text
cache invalidation
state refresh signal
publish changed-state notification
```

### CLASS 1 — SIMPLE MUTATION

Одна независимая запись без сложного FK/business chain.

Примеры:

```text
last_seen_at
sync timestamp
single metadata field
notification flag
```

### CLASS 2 — LOCAL TRANSACTION

2+ созависимых сущности в одной DB.

Пример:

```text
subscription
+ entitlements
+ resource limits
```

Одна SQLAlchemy transaction.

### CLASS 3 — DISTRIBUTED UPDATE

Несколько сервисов/DB.

Пример:

```text
Billing
→ Core
→ Resources
→ CRM projection
```

Outbox + orchestrator + gRPC + saga state.

---

## 18. Важно: сложность определяется не только числом таблиц

Правило `1 таблица = simple, 2+ = queue` слишком грубое.

Учитывать:

```text
dependency
business criticality
cross-service boundary
locking requirements
external side effects
retryability
```

Например `payment.status=PAID` формально одна таблица, но финансово критичная операция и обязана идти через защищенный UnitOfWork/outbox.

---

## 19. Приоритеты

Предлагаемые логические уровни:

```text
P0_CRITICAL = 100
P1_HIGH     = 75
P2_NORMAL   = 50
P3_LOW      = 25
P4_BULK     = 10
```

Примеры:

```text
P0: payment confirmed, subscription activation, security revoke
P1: entitlement/resource update after payment
P2: CRM projection, state propagation
P3: analytics, secondary notifications
P4: bulk rebuild, historical recalculation
```

---

## 20. Worker pools

```text
fast_pool
transaction_pool
distributed_pool
bulk_pool
```

### fast_pool

CLASS 0/1, высокая concurrency, короткие jobs.

### transaction_pool

CLASS 2, DB-aware, controlled concurrency, locks, SQLAlchemy transaction.

### distributed_pool

CLASS 3, gRPC, retries, saga state, service-health aware.

### bulk_pool

Тяжелые фоновые rebuild/recalc задачи. Не должен вытеснять payment activation.

---

## 21. Priority Dispatcher

Компонент:

```text
BillingJobDispatcher
```

На вход получает:

```text
event_type
mutation_class
priority
dependencies
ordering_key
```

и выбирает pool.

---

## 22. Синхронность данных и ordering

Многопуловая очередь нужна не для глобальной синхронности всех jobs, а для:

```text
ordering where required
resource isolation
priority
backpressure
safe concurrency
```

Для операций одного пользователя/заказа нужен общий ordering key:

```text
user:{user_id}
```

или:

```text
order:{order_id}
```

Разных пользователей можно обрабатывать параллельно, одного пользователя — сериализовать там, где порядок критичен.

---

## 23. DependencyRegistry

Компонент:

```text
BillingResourceDependencyRegistry
```

Пример dependency DAG:

```text
user
  ↓
subscription
  ↓
entitlements
  ↓
product_resources
  ↓
projections
```

Пример правила:

```python
ResourceRule(
    name="entitlements",
    depends_on=["subscription"],
    mutation_class="LOCAL_TRANSACTION",
    priority=75,
)
```

---

## 24. FK handling

Если одна DB:

```text
PostgreSQL FK
+ DependencyRegistry
+ SQLAlchemy flush
```

Flow:

```text
find/lock user
↓
subscription update
↓
flush()
↓
entitlements update
↓
flush()
↓
resources
↓
COMMIT
```

Если dependency ломается — rollback, без partial state.

---

## 25. Конкурентные обновления

Если два процесса меняют одного пользователя:

```text
payment callback
renewal
admin update
```

использовать:

```text
SELECT ... FOR UPDATE
```

или optimistic version:

```text
version = 14
```

При conflict:

```text
reload
rebuild UpdatePlan
retry
```

---

## 26. gRPC contract

Не делать десятки field-level методов:

```text
UpdateUserField
UpdateSubscriptionField
UpdateSearchField
```

Лучше command-oriented:

```text
ApplyBillingTransaction
```

Концепт:

```protobuf
message ApplyBillingTransactionRequest {
    string event_id = 1;
    string correlation_id = 2;
    string order_id = 3;
    string user_id = 4;
    string operation = 5;
    string plan_id = 6;
    SubscriptionMutation subscription = 7;
    repeated EntitlementMutation entitlements = 8;
    repeated ResourceMutation resources = 9;
}
```

---

## 27. Core gRPC handler

```text
request
↓
dedupe event_id
↓
find user
↓
lock/version check
↓
validate dependencies
↓
BEGIN local transaction
↓
subscription
↓
entitlements
↓
resources
↓
processed_events/inbox
↓
core_outbox
↓
COMMIT
```

---

## 28. Consumer Inbox / idempotency

Критичные consumers имеют таблицу:

```text
processed_events
```

Поля минимум:

```text
event_id UNIQUE
event_type
processed_at
result
```

Повторный delivery того же `event_id` не повторяет mutation.

---

## 29. Retry/backoff

Retryable:

```text
DB deadlock
serialization conflict
gRPC timeout
service unavailable
temporary DB/network error
```

Backoff, например логически:

```text
1s → 3s → 10s → 30s → 2m → 5m
```

После лимита:

```text
DEAD
```

+ alert.

Подтвержденный payment при этом остается `PAID`.

---

## 30. billing_sync_transactions

Для контроля распределенного применения:

```text
id
event_id
correlation_id
order_id
user_id
mutation_class
priority
status
core_status
resource_status
share_status
fiscal_status
attempt_count
next_attempt_at
last_error
created_at
updated_at
completed_at
```

Статусы:

```text
PENDING
RUNNING
PARTIAL
COMPLETED
FAILED_RETRYABLE
FAILED_FINAL
```

---

## 31. Subscription events

После commit Core создает durable domain events:

```text
subscription.activated
subscription.extended
subscription.changed
subscription.expired
entitlements.changed
resources.changed
```

После записи в outbox можно делать `NOTIFY core_events`.

---

## 32. Sync/Share

Разделить на два слоя.

### A. Event distribution

Для распространения факта изменения:

```text
payment.paid
subscription.changed
entitlements.changed
resources.changed
```

### B. REST State Sync

Для получения актуального состояния после restart/missed event:

```http
GET /v1/sync/users/{user_id}/snapshot
```

Пример:

```json
{
  "user_id": "...",
  "version": 42,
  "subscription": {},
  "entitlements": [],
  "resources": {},
  "updated_at": "..."
}
```

---

## 33. State versions

Желательно иметь:

```text
state_version
```

или:

```text
subscription_version
entitlements_version
resources_version
```

Consumer видит:

```text
local=40
server=42
→ refresh
```

---

## 34. Event envelope

```json
{
  "event_id": "uuid",
  "event_type": "subscription.changed",
  "event_version": 1,
  "correlation_id": "uuid",
  "user_id": "uuid",
  "order_id": "uuid",
  "aggregate_version": 42,
  "occurred_at": "...",
  "payload": {}
}
```

---

## 35. Correlation ID

Один payment flow трассируется целиком:

```text
Robokassa
↓
Billing
↓
Outbox
↓
Queue
↓
gRPC
↓
Core
↓
Resources
↓
Sync/Share
```

через один `correlation_id`.

---

## 36. LISTEN reconnect/recovery

Если listener потерял соединение:

```text
reconnect
↓
scan durable PENDING/RETRY outbox
↓
LISTEN again
```

Не пытаться использовать `NOTIFY` как историю событий.

---

## 37. Startup recovery

Dispatcher при старте:

```text
1. connect DB
2. scan PENDING/RETRY
3. recover expired leases
4. enqueue work
5. LISTEN channels
6. normal loop
```

---

## 38. Worker lease

Чтобы job не завис после crash:

```text
claimed_at
lease_until
worker_id
```

Если lease истек — job возвращается в retry.

---

## 39. Реализация очереди на PostgreSQL для MVP

Для первой версии допустимо:

```text
outbox table
+ SELECT ... FOR UPDATE SKIP LOCKED
+ worker pools
+ LISTEN/NOTIFY wakeup
```

Не обязательно сразу вводить Kafka/RabbitMQ.

Если нагрузка позже перерастет PostgreSQL queue, транспорт заменяется, а event contracts остаются.

---

## 40. Backpressure/fairness

`bulk_pool` не должен задерживать P0/P1.

Но низкие приоритеты не должны голодать вечно.

Нужна weighted/reserved capacity политика.

Конкретные concurrency/weights определяются нагрузочными тестами.

---

## 41. Не держать DB transaction во время gRPC

Плохо:

```text
BEGIN
lock rows
gRPC 10 sec
COMMIT
```

Правильно:

```text
local transaction
COMMIT
↓
outbox
↓
gRPC outside DB lock
```

---

## 42. Model ownership

Источник истины:

```text
Payment      → Billing
Subscription → Core
Entitlements → Core/Product owning module
CRM          → projection
Frontend     → no truth
```

Если DB разные, cross-service SQL FK не используется. Связь — логическая через `user_id` + contract validation.

---

## 43. Telegram / Extension / CRM

### Telegram

```text
Купить/Продлить
→ web checkout
→ payment
→ backend state
```

### Extension

```text
Купить/Продлить
→ lab-51.ru/billing
→ payment
→ refresh subscription snapshot
```

### CRM

Показывает:

```text
order
payment
subscription
activation_status
resource_sync_status
fiscal_status
```

CRM не ставит `PAID` вручную.

---

## 44. Reconciliation

Периодически сверять:

```text
Robokassa status
↔ Billing payment
↔ Core subscription
↔ resource sync
```

Искать:

```text
provider PAID / Billing not PAID
Billing PAID / subscription missing
subscription ACTIVE / resources stale
stuck queue jobs
dead jobs
```

---

## 45. Observability

Логировать минимум:

```text
request_id
correlation_id
event_id
order_id
user_id
mutation_class
priority
worker_pool
attempt
duration
result
```

Метрики:

```text
billing_outbox_pending
billing_outbox_dead
queue_depth_by_priority
queue_oldest_job_age
worker_active_by_pool
worker_failures_total
grpc_apply_latency
grpc_apply_failures
subscription_sync_pending
resource_sync_pending
listen_notify_reconnects
reconciliation_mismatch_total
```

---

## 46. Что Сане реализовать по порядку

```text
1. Зафиксировать DB topology: одна DB или несколько.
2. Зафиксировать ownership таблиц.
3. Описать protobuf ApplyBillingTransaction.
4. Сделать UpdatePlan.
5. Сделать TransactionAssembler.
6. Сделать DependencyRegistry.
7. Сделать transactional outbox.
8. Сделать PG dispatcher через SKIP LOCKED.
9. Добавить LISTEN/NOTIFY wake-up.
10. Добавить mutation classification.
11. Добавить priority routing.
12. Разделить worker pools.
13. Сделать gRPC consumer transaction.
14. Добавить processed_events/inbox.
15. Добавить retry/backoff/dead state.
16. Добавить core_outbox + subscription.changed.
17. Добавить Sync/Share snapshot REST.
18. Добавить reconciliation.
19. Добавить metrics/logging.
20. После этого стабильно подключать frontend Артёма.
```

---

## 47. P0

### Саня

- [ ] Billing models/migrations
- [ ] order/payment flow
- [ ] ResultURL validation
- [ ] UnitOfWork
- [ ] TransactionAssembler
- [ ] UpdatePlan
- [ ] DependencyRegistry
- [ ] transactional outbox
- [ ] PostgreSQL durable queue
- [ ] `SKIP LOCKED` workers
- [ ] LISTEN/NOTIFY wake-up
- [ ] priority/mutation classes
- [ ] worker pools
- [ ] gRPC `ApplyBillingTransaction`
- [ ] Core local transaction
- [ ] processed_events inbox
- [ ] retry/backoff
- [ ] worker leases
- [ ] correlation IDs
- [ ] subscription changed outbox
- [ ] Sync/Share REST snapshot
- [ ] reconciliation
- [ ] metrics/tests

### Артём

- [ ] register/login integration
- [ ] checkout
- [ ] quote/order
- [ ] redirect
- [ ] success/fail
- [ ] payment polling
- [ ] activation pending UI
- [ ] Telegram checkout link
- [ ] extension redirect
- [ ] CRM read-only status
- [ ] Sync snapshot consumption

---

## 48. Acceptance tests

### Payment

**T01** Valid callback → `PAID` + outbox event.

**T02** Invalid signature → no `PAID`, no activation.

**T03** Amount mismatch → no activation + alert.

**T04** Duplicate callback → one logical payment/activation.

### Outbox / LISTEN

**T05** DB committed, process died before queue publish → after restart outbox event still executes.

**T06** Listener offline during NOTIFY → reconnect + scan finds pending event.

### Priority

**T07** 100 bulk jobs + 1 payment activation → payment does not wait for all bulk jobs.

### Mutation classes

**T08** independent metadata update → fast pool.

**T09** subscription + entitlements same DB → one local ACID transaction.

**T10** Billing DB → Core DB → distributed pool + gRPC/outbox semantics.

### Dependencies/FK

**T11** child mutation before parent → registry orders correctly or transaction rolls back before commit.

### Concurrency

**T12** activate + extend same user concurrently → no lost update, correct ordering/version.

### Worker crash

**T13** claimed worker dies → lease expires → job retries.

### Core down

**T14** payment confirmed, Core unavailable → payment stays PAID, sync retries; after Core returns subscription becomes ACTIVE.

### gRPC dedupe

**T15** same `event_id` delivered twice → one mutation.

### Partial resource failure

**T16** same DB → rollback local transaction; different services → orchestration PARTIAL + retry.

### Subscription event

**T17** committed `subscription.changed` → durable outbox + NOTIFY + fresh Sync snapshot.

### Missed consumer signal

**T18** consumer offline → on restart full/current snapshot restores state.

### Frontend

**T19** success page opens before activation → «Платеж получен, активируем», not fake ACTIVE.

**T20** extension opens after payment → reads updated state from backend.

---

## 49. Финальный архитектурный вывод по последней идее Сани

Идея принимается с корректировкой:

### Да — `LISTEN/NOTIFY`

Использовать для мгновенного сигнала о committed update.

### Да — multi-pool priority queue

Использовать для разделения легких, локально-транзакционных, распределенных и bulk изменений.

### Да — TransactionAssembler

Использовать для формирования единого `UpdatePlan`.

### Да — DependencyRegistry

Использовать для порядка зависимых mutations и FK/invariant logic.

### Но — `LISTEN/NOTIFY` не durable queue

Истина о незавершенной работе живет в outbox/queue table.

### Но — не складывать ORM всех сервисов в Billing, если сервисы реально раздельные

Использовать:

```text
contracts
+ gRPC
+ local service ownership
+ outbox/saga
```

---

## 50. Definition of Done

Архитектура считается готовой, когда:

1. Robokassa callback валидируется Billing.
2. Payment state и outbox фиксируются атомарно.
3. Потеря NOTIFY не приводит к потере job.
4. Dispatcher восстанавливает pending work после restart.
5. Jobs имеют mutation class и priority.
6. Payment activation не блокируется bulk задачами.
7. Для одного user/order соблюдается required ordering.
8. TransactionAssembler формирует UpdatePlan.
9. DependencyRegistry учитывает зависимости/FK.
10. Same-DB изменения идут одной ACID transaction.
11. Cross-DB изменения идут через outbox + gRPC + saga/process state.
12. Consumers idempotent.
13. Worker crash безопасен.
14. Retry/backoff/dead state работают.
15. Subscription state порождает durable domain events.
16. LISTEN/NOTIFY только ускоряет доставку, но не является источником истины.
17. Sync/Share позволяет восстановить текущее состояние.
18. Website/TG/Extension/CRM получают один и тот же backend state.
19. Ownership Billing/Core/Resources зафиксирован.
20. Есть logs/metrics/correlation IDs/reconciliation.
21. Failure tests пройдены.
22. После test-mode проведен один контролируемый production payment.

---

## 51. Коротко для команды

**Саня строит:**

```text
payment truth
+ transaction orchestration
+ backend synchronization
```

**Артём подключает:**

```text
UI
→ стабильные API
→ отображение backend state
```

Архитектура должна позволять Артёму интегрировать сайт, Telegram, расширение и CRM без знания внутренних деталей Robokassa, SQLAlchemy-транзакций, очередей и gRPC.

---

**Конец ТЗ v4.**
