# LAB51 Central Auth Core

Центральная система аккаунтов LAB51 — единый `user_id` для сайта, Telegram-бота, браузерного расширения, CRM, платежей и всех будущих продуктов.

**Стек:** Python 3.14 + FastAPI + PostgreSQL 16 + Redis 7 + SQLAlchemy 2.0 (async) + Alembic + Argon2id + JWT

---

## Быстрый старт

### 1. Клонировать / перейти в проект

```bash
cd "saas backend"
```

### 2. Создать виртуальное окружение

```bash
python -m venv venv
venv\Scripts\activate     # Windows
# source venv/bin/activate  # Linux/Mac
```

### 3. Установить зависимости

```bash
pip install -r requirements.txt
```

### 4. Запустить PostgreSQL и Redis (Docker)

```bash
docker compose up -d
```

Проверить, что контейнеры запущены:

```bash
docker ps
```

Ожидаемый вывод: `lab51-postgres` (port 5439) и `lab51-redis` (port 6379) со статусом `healthy`.

### 5. Настроить `.env`

Файл `.env` уже создан. При необходимости отредактируй:

| Переменная | Значение по умолчанию | Описание |
|-----------|----------------------|----------|
| `DATABASE_URL` | `postgresql+asyncpg://lab51:lab51_secret@localhost:5439/lab51_auth` | Подключение к PostgreSQL |
| `REDIS_URL` | `redis://localhost:6379/0` | Подключение к Redis |
| `JWT_SECRET_KEY` | `lab51-dev-secret-key-...` | Секретный ключ для JWT |
| `ALLOWED_EMAIL_DOMAINS` | `mail.ru` | Разрешённые домены email |

### 6. Применить миграции

```bash
alembic upgrade head
```

Если миграция ещё не создана:

```bash
alembic revision --autogenerate -m "initial"
alembic upgrade head
```

### 7. Запустить сервер

```bash
python run.py
```

Сервер запустится на `http://localhost:8000`.

Swagger-документация: **http://localhost:8000/docs**

---

## Запуск тестов

```bash
# Убедись, что Docker-контейнеры запущены
docker compose up -d

# Создать тестовую БД (один раз)
docker exec lab51-postgres psql -U lab51 -d postgres -c "CREATE DATABASE lab51_auth_test;"

# Запустить тесты
pytest tests/test_auth.py -v --asyncio-mode=auto
```

---

## API Reference

Все эндпоинты доступны по адресу `http://localhost:8000/docs` (Swagger UI).

Базовый URL: `http://localhost:8000/v1`

### Аутентификация

| Метод | Путь | Описание |
|-------|------|----------|
| `POST` | `/v1/auth/register` | Регистрация нового пользователя |
| `POST` | `/v1/auth/login` | Вход по email/phone + пароль |
| `POST` | `/v1/auth/refresh` | Обновление access/refresh токенов |
| `POST` | `/v1/auth/logout` | Выход (отзыв refresh токена) |
| `POST` | `/v1/auth/send-otp` | Отправка OTP-кода |
| `POST` | `/v1/auth/verify-email` | Подтверждение email |
| `POST` | `/v1/auth/verify-phone` | Подтверждение телефона |
| `POST` | `/v1/auth/password/forgot` | Запрос сброса пароля |
| `POST` | `/v1/auth/password/reset` | Сброс пароля |
| `POST` | `/v1/auth/link/telegram/create` | Создать токен привязки Telegram |
| `POST` | `/v1/auth/link/telegram/confirm` | Подтвердить привязку Telegram |
| `POST` | `/v1/auth/telegram/find` | Найти пользователя по Telegram ID/phone |
| `POST` | `/v1/auth/telegram/link` | Прямая привязка Telegram к аккаунту |
| `GET` | `/v1/auth/sessions` | Список активных сессий |
| `DELETE` | `/v1/auth/sessions/{id}` | Отозвать сессию |

### Профиль пользователя

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/v1/me` | Получить профиль |
| `PATCH` | `/v1/me` | Обновить профиль (onboarding) |
| `GET` | `/v1/me/identities` | Способы идентификации |
| `GET` | `/v1/me/bonus` | Баланс бонусов |
| `GET` | `/v1/me/subscriptions` | Подписки |
| `GET` | `/v1/me/trials` | Пробные периоды |
| `GET` | `/v1/me/entitlements` | Права текущего тарифа |
| `GET` | `/v1/me/devices` | Устройства |

### Каталог

| Метод | Путь | Описание |
|-------|------|----------|
| `GET` | `/v1/catalog/categories` | Справочник категорий |

### Рефералы и CRM

| Метод | Путь | Описание |
|-------|------|----------|
| `POST` | `/v1/referrals` | Создать реферальную ссылку |
| `POST` | `/v1/referrals/click` | Записать клик по ссылке |
| `POST` | `/v1/referrals/leads` | Создать лид |
| `POST` | `/v1/referrals/attribution` | CRM-атрибуция (связать пользователя с лидом) |

---

## Примеры запросов

### Регистрация

```bash
curl -X POST http://localhost:8000/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@mail.ru",
    "password": "securepass123",
    "registration_source": "WEB"
  }'
```

Ответ:

```json
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "token_type": "bearer",
  "onboarding_required": true
}
```

### Вход

```bash
curl -X POST http://localhost:8000/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "identity": "user@mail.ru",
    "password": "securepass123"
  }'
```

### Завершение onboarding

```bash
curl -X PATCH http://localhost:8000/v1/me \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "Иван Петров",
    "avito_profile_url": "https://www.avito.ru/user/ivan123/profile",
    "primary_category_id": "00000000-0000-0000-0000-000000000001"
  }'
```

После этого автоматически:
- `onboarding_completed = true`
- Начисляется **+300 бонусов** (идемпотентно)
- Начинается **7-дневный trial**

### Получить entitlements

```bash
curl http://localhost:8000/v1/me/entitlements \
  -H "Authorization: Bearer <access_token>"
```

Ответ:

```json
{
  "entitlements": {
    "max_searches": 3,
    "regions": "BASIC",
    "telegram_bot": true,
    "extension": true,
    "voice": false,
    "devices": 1,
    "favorites": 50,
    "boost_daily": 0
  }
}
```

---

## Ошибки

Все ошибки возвращаются в едином формате:

```json
{
  "error": {
    "code": "IDENTITY_ALREADY_EXISTS",
    "message": "Account with this email already exists"
  }
}
```

### Коды ошибок

| Код | HTTP | Описание |
|-----|------|----------|
| `INVALID_CREDENTIALS` | 401 | Неверный email/phone или пароль |
| `ACCOUNT_BLOCKED` | 403 | Аккаунт заблокирован |
| `IDENTITY_ALREADY_EXISTS` | 409 | Email/phone/Telegram уже используется |
| `LINK_TOKEN_EXPIRED` | 400 | Токен привязки истёк |
| `LINK_TOKEN_INVALID` | 400 | Токен привязки недействителен |
| `ONBOARDING_REQUIRED` | 403 | Не пройден onboarding |
| `TRIAL_ALREADY_USED` | 409 | Trial уже использован |
| `ENTITLEMENT_LIMIT_REACHED` | 403 | Лимит тарифа исчерпан |
| `TOKEN_EXPIRED` | 401 | Токен истёк |
| `TOKEN_INVALID` | 401 | Токен недействителен |
| `RATE_LIMIT_EXCEEDED` | 429 | Превышен лимит запросов |
| `NOT_FOUND` | 404 | Ресурс не найден |

---

## Архитектура

```
app/
├── main.py              # FastAPI приложение
├── config.py            # Настройки (pydantic-settings)
├── database.py          # Async SQLAlchemy engine
├── dependencies.py      # DI: get_current_user, get_client_info
├── models.py            # 19 таблиц (SQLAlchemy ORM)
│
├── core/
│   ├── security.py      # Argon2id, JWT, OTP
│   ├── redis.py         # Redis клиент
│   ├── exceptions.py    # Error contract
│   ├── rate_limit.py    # Rate limiting
│   └── audit.py         # Аудит
│
├── utils/
│   └── normalizers.py   # Email, phone (E.164), Avito URL
│
└── modules/
    ├── auth/            # Аутентификация
    ├── users/           # Профиль, onboarding, бонусы
    ├── catalog/         # Категории
    ├── referrals/       # Рефералы, CRM
    └── events/          # Transactional outbox
```

### Ключевые принципы

- **Один `user_id`** — для всех интерфейсов (сайт, бот, расширение)
- **`user_identities`** — email/phone/Telegram отдельно от пользователя
- **Argon2id** — хеширование паролей
- **JWT** — access token (15 min) + refresh token (30 days) с rotation
- **Transactional Outbox** — события создаются в той же транзакции
- **Idempotent bonus** — `UNIQUE(user_id, type)` для +300
- **One trial per user** — проверка перед созданием
- **Rate limiting** — Redis sliding window

---

## База данных

19 таблиц:

| Таблица | Назначение |
|---------|-----------|
| `users` | Центральная таблица пользователей |
| `user_identities` | Email, phone, Telegram, Google, Apple |
| `user_credentials` | Argon2id хеши паролей |
| `user_sessions` | Refresh tokens |
| `user_devices` | Устройства |
| `account_link_tokens` | Токены привязки Telegram |
| `categories` | Справочник категорий |
| `bonus_ledger` | Бухгалтерия бонусов |
| `products` / `plans` | Продукты и тарифы |
| `entitlements` | Права тарифов |
| `trials` | Пробные периоды |
| `subscriptions` | Подписки |
| `referrals` / `referral_clicks` | Реферальная система |
| `leads` / `attributions` | CRM |
| `outbox_events` | Transactional outbox |
| `audit_log` | Аудит |