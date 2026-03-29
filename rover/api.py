"""Rover API — FastAPI backend for the SaaS dashboard."""

import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from rover.deps import get_current_user, get_db, get_token_store, get_user_id
from rover.logger import get_logger, setup_logging

load_dotenv()

logger = get_logger("api")

app = FastAPI(title="Rover API", version="0.1.0")

# CORS — allow dashboard origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://tryrover.app",
        "https://www.tryrover.app",
        "https://rover-web.vercel.app",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "rover-api"}


# ------------------------------------------------------------------
# Gmail OAuth
# ------------------------------------------------------------------


@app.post("/api/auth/gmail/connect")
async def gmail_connect(
    request: Request,
    user_id: str = Depends(get_user_id),
):
    """Start Gmail OAuth flow — returns the Google authorization URL."""
    from rover.gmail import get_auth_url

    redirect_uri = os.environ.get(
        "GMAIL_OAUTH_REDIRECT_URI",
        "http://localhost:8000/api/auth/gmail/callback",
    )

    # Encode return path in state so callback knows where to redirect
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    return_to = body.get("return_to", "/dashboard/settings")
    state = f"{user_id}|{return_to}"

    auth_url = get_auth_url(redirect_uri=redirect_uri, state=state)
    return {"auth_url": auth_url}


@app.get("/api/auth/gmail/callback")
async def gmail_callback(
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    """Handle Google OAuth callback — exchange code for tokens and store them."""
    if error:
        logger.warning("Gmail OAuth error: %s", error)
        # Redirect to dashboard with error
        dashboard_url = os.environ.get("DASHBOARD_URL", "http://localhost:3000")
        return RedirectResponse(f"{dashboard_url}/dashboard/settings?gmail_error={error}")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state parameter")

    from rover.gmail import handle_callback

    redirect_uri = os.environ.get(
        "GMAIL_OAUTH_REDIRECT_URI",
        "http://localhost:8000/api/auth/gmail/callback",
    )

    # Parse state: "user_id|return_path" or just "user_id"
    if "|" in state:
        user_id, return_to = state.split("|", 1)
    else:
        user_id = state
        return_to = "/dashboard/settings"

    token_store = get_token_store()
    dashboard_url = os.environ.get("DASHBOARD_URL", "http://localhost:3000")

    try:
        gmail_email = handle_callback(
            authorization_response=f"{redirect_uri}?code={code}",
            redirect_uri=redirect_uri,
            user_id=user_id,
            token_store=token_store,
        )
    except Exception:
        logger.exception("Gmail OAuth callback failed for user %s", user_id)
        return RedirectResponse(f"{dashboard_url}{return_to}?gmail_error=callback_failed")

    return RedirectResponse(f"{dashboard_url}{return_to}?gmail_connected=true")


@app.get("/api/auth/gmail/status")
async def gmail_status(user_id: str = Depends(get_user_id)):
    """Check if the user has connected their Gmail account."""
    token_store = get_token_store()
    connected = token_store.has_token(user_id)

    gmail_email = None
    if connected:
        row = get_db().get_gmail_token(user_id)
        if row:
            gmail_email = row.get("gmail_email")

    return {"connected": connected, "gmail_email": gmail_email}


@app.delete("/api/auth/gmail/disconnect")
async def gmail_disconnect(user_id: str = Depends(get_user_id)):
    """Disconnect Gmail — removes stored OAuth tokens."""
    token_store = get_token_store()
    token_store.delete_token(user_id)
    return {"disconnected": True}


# ------------------------------------------------------------------
# Onboarding
# ------------------------------------------------------------------


@app.get("/api/onboarding/status")
async def onboarding_status(user_id: str = Depends(get_user_id)):
    """Check if the user has completed onboarding."""
    db = get_db()
    completed = db.get_metadata(user_id, "onboarding_completed")
    return {"completed": completed == "true"}


@app.post("/api/onboarding/complete")
async def onboarding_complete(user_id: str = Depends(get_user_id)):
    """Mark onboarding as completed."""
    db = get_db()
    db.set_metadata(user_id, "onboarding_completed", "true")
    return {"completed": True}


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------


@app.get("/api/dashboard/summary")
async def dashboard_summary(user_id: str = Depends(get_user_id)):
    """Aggregate stats for the dashboard overview."""
    db = get_db()
    summary = db.get_dashboard_summary(user_id)

    # Add Gmail connection status
    token_store = get_token_store()
    summary["gmail_connected"] = token_store.has_token(user_id)

    return summary


# ------------------------------------------------------------------
# Purchases
# ------------------------------------------------------------------


@app.post("/api/purchases")
async def create_purchase(body: dict, user_id: str = Depends(get_user_id)):
    """Manually add a purchase to track."""
    product_url = body.get("product_url")
    if not product_url:
        raise HTTPException(status_code=400, detail="product_url is required")

    from datetime import date
    import uuid

    db = get_db()
    purchase_id = db.add_purchase(
        user_id=user_id,
        gmail_message_id=f"manual_{uuid.uuid4().hex[:12]}",
        item_name=body.get("item_name") or "Untitled product",
        price_paid=float(body["price_paid"]) if body.get("price_paid") else 0,
        product_url=product_url,
        retailer=body.get("retailer") or "",
        purchase_date=body.get("purchase_date") or date.today().isoformat(),
        currency=body.get("currency", "USD"),
        order_number=body.get("order_number"),
    )

    if not purchase_id:
        raise HTTPException(status_code=400, detail="Failed to create purchase")

    return {"id": purchase_id, "created": True}


@app.get("/api/purchases")
async def list_purchases(user_id: str = Depends(get_user_id)):
    """List all purchases with their latest price check status."""
    db = get_db()
    purchases = db.get_purchases_with_latest_check(user_id)

    # Serialize datetimes to strings
    for p in purchases:
        for key, val in p.items():
            if hasattr(val, "isoformat"):
                p[key] = val.isoformat()

    return {"purchases": purchases, "count": len(purchases)}


@app.get("/api/purchases/{purchase_id}")
async def get_purchase(purchase_id: int, user_id: str = Depends(get_user_id)):
    """Get a single purchase with full price check history."""
    db = get_db()
    purchase = db.get_purchase(purchase_id)

    if not purchase:
        raise HTTPException(status_code=404, detail="Purchase not found")

    # Verify this purchase belongs to the authenticated user
    if str(purchase.get("user_id")) != user_id:
        raise HTTPException(status_code=404, detail="Purchase not found")

    # Get price check history
    with db._cursor() as cur:
        cur.execute(
            "SELECT * FROM price_checks WHERE purchase_id = %s ORDER BY checked_at DESC",
            (purchase_id,),
        )
        price_checks = [dict(r) for r in cur.fetchall()]

    # Get savings for this purchase
    with db._cursor() as cur:
        cur.execute(
            "SELECT * FROM savings WHERE purchase_id = %s ORDER BY detected_at DESC",
            (purchase_id,),
        )
        savings = [dict(r) for r in cur.fetchall()]

    # Serialize datetimes
    for item in [purchase] + price_checks + savings:
        for key, val in item.items():
            if hasattr(val, "isoformat"):
                item[key] = val.isoformat()

    return {
        "purchase": purchase,
        "price_checks": price_checks,
        "savings": savings,
    }


# ------------------------------------------------------------------
# Savings
# ------------------------------------------------------------------


@app.get("/api/savings")
async def list_savings(user_id: str = Depends(get_user_id)):
    """List all detected price drops with purchase details."""
    db = get_db()
    savings = db.get_savings_with_details(user_id)

    for s in savings:
        for key, val in s.items():
            if hasattr(val, "isoformat"):
                s[key] = val.isoformat()

    return {"savings": savings, "count": len(savings)}


# ------------------------------------------------------------------
# User profile
# ------------------------------------------------------------------


@app.get("/api/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Return the current user's profile."""
    for key, val in user.items():
        if hasattr(val, "isoformat"):
            user[key] = val.isoformat()
    return user


@app.delete("/api/me")
async def delete_me(user: dict = Depends(get_current_user)):
    """Delete the current user's account and all associated data."""
    db = get_db()
    user_id = str(user["id"])

    # Remove Gmail tokens first
    token_store = get_token_store()
    try:
        token_store.delete_token(user_id)
    except Exception:
        pass

    db.delete_user(user_id)
    return {"deleted": True}


@app.patch("/api/me")
async def update_me(
    updates: dict,
    user: dict = Depends(get_current_user),
):
    """Update the current user's name."""
    db = get_db()
    user_id = str(user["id"])

    name = updates.get("name")
    if name is not None:
        with db._cursor() as cur:
            cur.execute("UPDATE users SET name = %s WHERE id = %s", (name, user_id))

    updated = db.get_user(user_id)
    for key, val in updated.items():
        if hasattr(val, "isoformat"):
            updated[key] = val.isoformat()
    return updated


# ------------------------------------------------------------------
# Notifications
# ------------------------------------------------------------------


@app.get("/api/notifications")
async def list_notifications(user_id: str = Depends(get_user_id)):
    """List notifications for the current user."""
    db = get_db()
    notifications = db.get_notifications(user_id)
    unread = db.get_unread_notification_count(user_id)

    for n in notifications:
        for key, val in n.items():
            if hasattr(val, "isoformat"):
                n[key] = val.isoformat()

    return {"notifications": notifications, "unread_count": unread}


@app.post("/api/notifications/{notification_id}/read")
async def mark_read(notification_id: int, user_id: str = Depends(get_user_id)):
    """Mark a single notification as read."""
    db = get_db()
    db.mark_notification_read(notification_id, user_id)
    return {"ok": True}


@app.post("/api/notifications/read-all")
async def mark_all_read(user_id: str = Depends(get_user_id)):
    """Mark all notifications as read."""
    db = get_db()
    db.mark_all_notifications_read(user_id)
    return {"ok": True}


# ------------------------------------------------------------------
# Activity feed
# ------------------------------------------------------------------


@app.get("/api/activity")
async def get_activity(user_id: str = Depends(get_user_id)):
    """Get recent activity for the user — price checks, savings, notifications."""
    db = get_db()
    activity = []

    # Recent price checks
    with db._cursor() as cur:
        cur.execute(
            """SELECT pc.*, p.item_name, p.retailer
               FROM price_checks pc
               JOIN purchases p ON pc.purchase_id = p.id
               WHERE p.user_id = %s
               ORDER BY pc.checked_at DESC LIMIT 30""",
            (user_id,),
        )
        for row in cur.fetchall():
            r = dict(row)
            activity.append({
                "type": "price_check",
                "title": f"Checked price for {r['item_name']}",
                "detail": f"${r['current_price']:.2f}" if r.get("current_price") else r.get("error_detail", "Failed"),
                "status": r["status"],
                "retailer": r["retailer"],
                "timestamp": r["checked_at"].isoformat() if hasattr(r["checked_at"], "isoformat") else r["checked_at"],
            })

    # Recent savings
    with db._cursor() as cur:
        cur.execute(
            """SELECT s.*, p.item_name, p.retailer
               FROM savings s
               JOIN purchases p ON s.purchase_id = p.id
               WHERE p.user_id = %s
               ORDER BY s.detected_at DESC LIMIT 20""",
            (user_id,),
        )
        for row in cur.fetchall():
            r = dict(row)
            activity.append({
                "type": "saving",
                "title": f"Price drop on {r['item_name']}",
                "detail": f"Save ${r['savings_amount']:.2f}",
                "status": r["status"],
                "retailer": r["retailer"],
                "timestamp": r["detected_at"].isoformat() if hasattr(r["detected_at"], "isoformat") else r["detected_at"],
            })

    # Sort by timestamp descending
    activity.sort(key=lambda a: a["timestamp"], reverse=True)

    return {"activity": activity[:50]}
