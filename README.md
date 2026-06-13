# Stripe to Discord Webhook With SQLite

A small Python FastAPI service that receives Stripe webhook events, verifies Stripe’s signature, saves every event to SQLite, and forwards a readable notification to a Discord webhook.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in the values:
   - `STRIPE_WEBHOOK_SECRET` — your Stripe webhook endpoint secret
   - `DISCORD_WEBHOOK_URL` — your Discord incoming webhook URL
   - `DATABASE_PATH` — SQLite file path (defaults to `stripe_events.db`)
   - `STRIPE_SECRET_KEY` — optional, only needed for other Stripe API calls

3. Run the server:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

## Webhook endpoint

- `POST /stripe/webhook`

Stripe must send raw JSON bodies and include the `Stripe-Signature` header.

## Behavior

- Invalid signatures or payloads → `400`
- Missing `STRIPE_WEBHOOK_SECRET` → `500`
- Valid events → saved to SQLite, forwarded to Discord, then `200`
- Duplicate Stripe event IDs are ignored (idempotent)
- If Discord fails, the event is still saved and `200` is returned so Stripe does not retry

## Testing with Stripe CLI

1. Start the server:
   ```bash
   uvicorn main:app --port 8000
   ```

2. Forward Stripe events:
   ```bash
   stripe listen --forward-to localhost:8000/stripe/webhook
   ```

3. Trigger test events:
   ```bash
   stripe trigger payment_intent.succeeded
   stripe trigger payment_intent.payment_failed
   ```

## Database schema (SQLite)

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| stripe_event_id | TEXT | Unique Stripe event ID |
| event_type | TEXT | Stripe event type |
| created_timestamp | INTEGER | Stripe event created timestamp |
| payload | TEXT | Full JSON payload |
| discord_status | TEXT | `sent` or `failed` |
| discord_response | TEXT | Raw Discord response or error |
| inserted_at | INTEGER | When the row was inserted |
| processed_at | INTEGER | When Discord was updated |
