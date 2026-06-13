import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager

import httpx
import stripe
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse

STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "stripe_events.db")

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")


def init_db() -> None:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stripe_event_id TEXT UNIQUE NOT NULL,
            event_type TEXT NOT NULL,
            created_timestamp INTEGER,
            payload TEXT NOT NULL,
            discord_status TEXT,
            discord_response TEXT,
            inserted_at INTEGER DEFAULT (unixepoch()),
            processed_at INTEGER
        )
        """
    )
    conn.commit()
    conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(lifespan=lifespan)


def _save_event(
    stripe_event_id: str,
    event_type: str,
    created_timestamp: int | None,
    payload: str,
) -> bool:
    """Insert the event into SQLite, ignoring duplicates. Returns True if inserted."""
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute(
            """
            INSERT INTO events (stripe_event_id, event_type, created_timestamp, payload)
            VALUES (?, ?, ?, ?)
            """,
            (stripe_event_id, event_type, created_timestamp, payload),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def _update_discord_status(
    stripe_event_id: str, status: str, response: str | None = None
) -> None:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute(
        """
        UPDATE events
        SET discord_status = ?, discord_response = ?, processed_at = ?
        WHERE stripe_event_id = ?
        """,
        (status, response, int(time.time()), stripe_event_id),
    )
    conn.commit()
    conn.close()


def _build_discord_embed(event: stripe.Event) -> dict:
    data = event.data.get("object", {}) if event.data else {}
    event_type = event.type or "unknown"
    created = event.created

    fields = []
    if "amount" in data:
        fields.append(
            {
                "name": "Amount",
                "value": str(data["amount"]),
                "inline": True,
            }
        )
    if "amount_received" in data:
        fields.append(
            {
                "name": "Amount Received",
                "value": str(data["amount_received"]),
                "inline": True,
            }
        )
    if "amount_total" in data:
        fields.append(
            {
                "name": "Amount Total",
                "value": str(data["amount_total"]),
                "inline": True,
            }
        )
    if "customer" in data:
        fields.append(
            {
                "name": "Customer",
                "value": str(data["customer"]),
                "inline": True,
            }
        )
    if "customer_email" in data:
        fields.append(
            {
                "name": "Customer Email",
                "value": str(data["customer_email"]),
                "inline": True,
            }
        )
    if "payment_intent" in data:
        fields.append(
            {
                "name": "Payment Intent",
                "value": str(data["payment_intent"]),
                "inline": True,
            }
        )
    if "currency" in data:
        fields.append(
            {
                "name": "Currency",
                "value": str(data["currency"]).upper(),
                "inline": True,
            }
        )
    if "status" in data:
        fields.append(
            {
                "name": "Status",
                "value": str(data["status"]),
                "inline": True,
            }
        )

    embed = {
        "title": f"Stripe Event: {event_type}",
        "color": 0x635BFF,
        "fields": [
            {"name": "Event ID", "value": event.id, "inline": False},
            {"name": "Object Type", "value": data.get("object", "unknown"), "inline": True},
            {
                "name": "Created",
                "value": f"<t:{created}:F>" if created else "N/A",
                "inline": True,
            },
            *fields,
        ],
        "footer": {"text": "Stripe → Discord"},
    }
    return {"embeds": [embed]}


async def _send_discord(event: stripe.Event) -> tuple[bool, str]:
    if not DISCORD_WEBHOOK_URL:
        return False, "DISCORD_WEBHOOK_URL not configured"

    payload = _build_discord_embed(event)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(DISCORD_WEBHOOK_URL, json=payload)
            body = resp.text
            if resp.status_code >= 200 and resp.status_code < 300:
                return True, body
            return False, f"HTTP {resp.status_code}: {body}"
    except Exception as exc:
        return False, str(exc)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    if not STRIPE_WEBHOOK_SECRET:
        return JSONResponse(
            status_code=500, content={"detail": "STRIPE_WEBHOOK_SECRET not configured"}
        )

    body = await request.body()
    try:
        event = stripe.Webhook.construct_event(body, stripe_signature, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        return JSONResponse(status_code=400, content={"detail": "Invalid signature"})
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "Invalid payload"})

    payload_json = json.dumps(event.to_dict(), default=str)
    inserted = _save_event(
        stripe_event_id=event.id,
        event_type=event.type,
        created_timestamp=event.created,
        payload=payload_json,
    )

    if not inserted:
        # Idempotent: already processed
        return Response(status_code=200)

    success, discord_response = await _send_discord(event)
    status = "sent" if success else "failed"
    _update_discord_status(event.id, status, discord_response)

    return Response(status_code=200)
