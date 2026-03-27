"""Lightweight dev server for testing Rover's pipeline interactively."""

import json
import traceback
from datetime import date

import anthropic
from flask import Flask, jsonify, render_template_string, request

from rover.config import get_config
from rover.db import Database
from rover.gmail import GmailClient
from rover.logger import setup_logging
from rover.parser import ReceiptParser
from rover.policies import PolicyLookup
from rover.scraper import Scraper

app = Flask(__name__)

# --- Globals initialized on startup ---
config = None
db = None
gmail = None
parser = None
scraper = None
policy_lookup = None


def init():
    global config, db, gmail, parser, scraper, policy_lookup
    config = get_config()
    setup_logging(config)
    db = Database(config.get("database", {}).get("path", "rover.db"))
    # Allow SQLite connection to be used across Flask's request threads
    db.conn.close()
    import sqlite3
    db.conn = sqlite3.connect(db.db_path, check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    db.conn.execute("PRAGMA journal_mode=WAL")
    db.conn.execute("PRAGMA foreign_keys=ON")
    gmail = GmailClient(config)
    parser = ReceiptParser(config)
    scraper = Scraper(config)

    llm_client = anthropic.Anthropic()
    llm_model = config.get("anthropic", {}).get("model", "claude-sonnet-4-20250514")
    policy_lookup = PolicyLookup(
        db=db,
        scraper=scraper,
        llm_client=llm_client,
        llm_model=llm_model,
        retailers_yaml_path=config.get("retailers_yaml", "retailers.yaml"),
        default_window=config.get("default_refund_window_days", 14),
    )


# ---- HTML Template ----

TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<title>Rover Dev</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0a0a0a; color: #e0e0e0; padding: 24px; max-width: 960px; margin: 0 auto; }
  h1 { font-size: 1.4rem; margin-bottom: 24px; color: #fff; }
  h2 { font-size: 1.1rem; margin-bottom: 12px; color: #ccc; border-bottom: 1px solid #222; padding-bottom: 6px; }
  .section { background: #141414; border: 1px solid #222; border-radius: 8px; padding: 20px; margin-bottom: 20px; }
  button { background: #2563eb; color: #fff; border: none; padding: 8px 18px; border-radius: 6px;
           cursor: pointer; font-size: 0.9rem; }
  button:hover { background: #1d4ed8; }
  button:disabled { background: #333; cursor: wait; }
  input[type=text] { background: #1a1a1a; border: 1px solid #333; color: #e0e0e0; padding: 8px 12px;
                     border-radius: 6px; font-size: 0.9rem; width: 300px; }
  .status { margin-top: 10px; font-size: 0.85rem; color: #888; }
  .status.ok { color: #22c55e; }
  .status.err { color: #ef4444; }
  .results { margin-top: 16px; }
  .card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 6px; padding: 14px; margin-bottom: 10px; }
  .card .label { font-size: 0.75rem; color: #666; text-transform: uppercase; letter-spacing: 0.05em; }
  .card .value { font-size: 0.9rem; color: #e0e0e0; margin-bottom: 8px; word-break: break-all; }
  .card .value a { color: #60a5fa; text-decoration: none; }
  .card .value a:hover { text-decoration: underline; }
  .tag { display: inline-block; font-size: 0.75rem; padding: 2px 8px; border-radius: 4px; margin-right: 4px; }
  .tag.receipt { background: #166534; color: #86efac; }
  .tag.skip { background: #44403c; color: #a8a29e; }
  .tag.parsed { background: #1e3a5f; color: #93c5fd; }
  .row { display: flex; gap: 12px; flex-wrap: wrap; }
  .row > div { flex: 1; min-width: 140px; }
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #555;
             border-top-color: #2563eb; border-radius: 50%; animation: spin 0.6s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .link-list { list-style: none; padding: 0; }
  .link-list li { padding: 6px 0; border-bottom: 1px solid #1a1a1a; font-size: 0.9rem; }
  .link-list li:last-child { border-bottom: none; }
</style>
</head>
<body>
<h1>Rover Dev</h1>

<!-- Gmail Connection -->
<div class="section">
  <h2>1. Gmail Connection</h2>
  <button onclick="connectGmail()" id="btn-gmail">Connect Gmail</button>
  <div class="status" id="gmail-status"></div>
</div>

<!-- Email Scan -->
<div class="section">
  <h2>2. Scan Emails</h2>
  <p style="font-size:0.85rem;color:#888;margin-bottom:12px;">Fetches recent emails, classifies them, and parses receipts.</p>
  <button onclick="scanEmails()" id="btn-scan">Scan Inbox</button>
  <div class="status" id="scan-status"></div>
  <div class="results" id="scan-results"></div>
</div>

<!-- Stored Purchases -->
<div class="section">
  <h2>3. Stored Purchases</h2>
  <button onclick="loadPurchases()" id="btn-purchases">Load from DB</button>
  <div class="status" id="purchases-status"></div>
  <div class="results" id="purchases-results"></div>
</div>

<!-- Policy Lookup -->
<div class="section">
  <h2>4. Policy Link Discovery</h2>
  <p style="font-size:0.85rem;color:#888;margin-bottom:12px;">Scrapes a retailer homepage footer for return/refund policy links.</p>
  <div style="display:flex;gap:8px;align-items:center;">
    <input type="text" id="domain-input" placeholder="e.g. bestbuy.com">
    <button onclick="lookupPolicy()" id="btn-policy">Find Policy Links</button>
  </div>
  <div class="status" id="policy-status"></div>
  <div class="results" id="policy-results"></div>
</div>

<script>
function setStatus(id, msg, cls) {
  const el = document.getElementById(id);
  el.className = 'status ' + (cls || '');
  el.innerHTML = msg;
}

async function connectGmail() {
  const btn = document.getElementById('btn-gmail');
  btn.disabled = true;
  setStatus('gmail-status', '<span class="spinner"></span> Authenticating...');
  try {
    const res = await fetch('/api/gmail/connect', {method: 'POST'});
    const data = await res.json();
    setStatus('gmail-status', data.ok ? 'Connected' : 'Error: ' + data.error, data.ok ? 'ok' : 'err');
  } catch(e) {
    setStatus('gmail-status', 'Request failed: ' + e, 'err');
  }
  btn.disabled = false;
}

async function scanEmails() {
  const btn = document.getElementById('btn-scan');
  btn.disabled = true;
  setStatus('scan-status', '<span class="spinner"></span> Scanning (this may take a minute)...');
  document.getElementById('scan-results').innerHTML = '';
  try {
    const res = await fetch('/api/emails/scan', {method: 'POST'});
    const data = await res.json();
    if (!data.ok) { setStatus('scan-status', 'Error: ' + data.error, 'err'); btn.disabled = false; return; }
    setStatus('scan-status',
      `Found ${data.total_emails} emails, ${data.receipts_found} receipts, ${data.purchases_stored} new purchases`, 'ok');
    const container = document.getElementById('scan-results');
    let html = '';
    for (const e of data.emails) {
      const tag = e.is_receipt ? '<span class="tag receipt">receipt</span>' : '<span class="tag skip">' + esc(e.skip_reason || 'skipped') + '</span>';
      html += '<div class="card">';
      html += '<div class="row"><div><div class="label">Subject</div><div class="value">' + esc(e.subject) + ' ' + tag + '</div></div></div>';
      html += '<div class="row"><div><div class="label">From</div><div class="value">' + esc(e.from) + '</div></div>';
      html += '<div><div class="label">Date</div><div class="value">' + esc(e.date) + '</div></div></div>';
      if (e.receipt) {
        const r = e.receipt;
        html += '<div class="row">';
        html += '<div><div class="label">Item</div><div class="value">' + esc(r.item_name) + '</div></div>';
        html += '<div><div class="label">Price</div><div class="value">$' + r.price_paid.toFixed(2) + '</div></div>';
        html += '<div><div class="label">Retailer</div><div class="value">' + esc(r.retailer) + '</div></div>';
        html += '</div>';
        if (r.product_url) {
          html += '<div><div class="label">Product URL</div><div class="value"><a href="' + esc(r.product_url) + '" target="_blank">' + esc(r.product_url) + '</a></div></div>';
        }
      }
      html += '</div>';
    }
    container.innerHTML = html;
  } catch(e) {
    setStatus('scan-status', 'Request failed: ' + e, 'err');
  }
  btn.disabled = false;
}

async function loadPurchases() {
  const btn = document.getElementById('btn-purchases');
  btn.disabled = true;
  setStatus('purchases-status', '<span class="spinner"></span> Loading...');
  try {
    const res = await fetch('/api/purchases');
    const data = await res.json();
    setStatus('purchases-status', data.purchases.length + ' purchases in DB', 'ok');
    const container = document.getElementById('purchases-results');
    let html = '';
    for (const p of data.purchases) {
      html += '<div class="card"><div class="row">';
      html += '<div><div class="label">Item</div><div class="value">' + esc(p.item_name) + '</div></div>';
      html += '<div><div class="label">Price</div><div class="value">$' + (p.price_paid||0).toFixed(2) + '</div></div>';
      html += '<div><div class="label">Retailer</div><div class="value">' + esc(p.retailer) + '</div></div>';
      html += '<div><div class="label">Date</div><div class="value">' + esc(p.purchase_date) + '</div></div>';
      html += '</div>';
      if (p.product_url) {
        html += '<div><div class="label">Product URL</div><div class="value"><a href="' + esc(p.product_url) + '" target="_blank">' + esc(p.product_url) + '</a></div></div>';
      }
      html += '</div>';
    }
    container.innerHTML = html || '<div style="color:#666;font-size:0.85rem;">No purchases yet. Run an email scan first.</div>';
  } catch(e) {
    setStatus('purchases-status', 'Request failed: ' + e, 'err');
  }
  btn.disabled = false;
}

async function lookupPolicy() {
  const domain = document.getElementById('domain-input').value.trim();
  if (!domain) return;
  const btn = document.getElementById('btn-policy');
  btn.disabled = true;
  setStatus('policy-status', '<span class="spinner"></span> Fetching homepage & scanning footer...');
  document.getElementById('policy-results').innerHTML = '';
  try {
    const res = await fetch('/api/policy/discover', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({domain})});
    const data = await res.json();
    if (!data.ok) {
      setStatus('policy-status', data.error, 'err');
      const container = document.getElementById('policy-results');
      let html = '';
      if (data.existing_retailer) {
        const r = data.existing_retailer;
        html += '<div class="card"><div class="label">Known retailer (from DB)</div>';
        html += '<div class="row" style="margin-top:8px;">';
        html += '<div><div class="label">Name</div><div class="value">' + esc(r.name) + '</div></div>';
        html += '<div><div class="label">Refund Window</div><div class="value">' + r.refund_window_days + ' days</div></div>';
        html += '<div><div class="label">Source</div><div class="value">' + esc(r.source) + '</div></div>';
        html += '</div>';
        if (r.policy_url) html += '<div><div class="label">Policy URL</div><div class="value"><a href="' + esc(r.policy_url) + '" target="_blank">' + esc(r.policy_url) + '</a></div></div>';
        html += '</div>';
      }
      container.innerHTML = html;
      btn.disabled = false; return;
    }
    const container = document.getElementById('policy-results');
    let html = '';
    {
      setStatus('policy-status',
        `Found ${data.footer_links_total} footer links, ${data.policy_links.length} policy-related`, 'ok');
      if (data.existing_retailer) {
        const r = data.existing_retailer;
        html += '<div class="card" style="border-color:#333;"><div class="label">Already in DB</div>';
        html += '<div class="row" style="margin-top:8px;">';
        html += '<div><div class="label">Refund Window</div><div class="value">' + r.refund_window_days + ' days</div></div>';
        html += '<div><div class="label">Source</div><div class="value">' + esc(r.source) + '</div></div>';
        html += '</div></div>';
      }
      if (data.policy_links.length) {
        html += '<ul class="link-list">';
        for (const l of data.policy_links) {
          html += '<li><a href="' + esc(l.href) + '" target="_blank" style="color:#60a5fa;">' + esc(l.text || '(no text)') + '</a> <span style="color:#555;font-size:0.8rem;">' + esc(l.href) + '</span></li>';
        }
        html += '</ul>';
      }
      if (data.all_footer_links && data.all_footer_links.length) {
        html += '<details style="margin-top:12px;"><summary style="color:#888;cursor:pointer;font-size:0.85rem;">All footer links (' + data.all_footer_links.length + ')</summary><ul class="link-list" style="margin-top:8px;">';
        for (const l of data.all_footer_links) {
          html += '<li><a href="' + esc(l.href) + '" target="_blank" style="color:#60a5fa;">' + esc(l.text || '(no text)') + '</a> <span style="color:#555;font-size:0.8rem;">' + esc(l.href) + '</span></li>';
        }
        html += '</ul></details>';
      }
    }
    container.innerHTML = html || '<div style="color:#666;">No links found.</div>';
  } catch(e) {
    setStatus('policy-status', 'Request failed: ' + e, 'err');
  }
  btn.disabled = false;
}

function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
</script>
</body>
</html>
"""


# ---- Routes ----

@app.route("/")
def index():
    return render_template_string(TEMPLATE)


@app.route("/api/gmail/connect", methods=["POST"])
def gmail_connect():
    try:
        gmail.authenticate()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/emails/scan", methods=["POST"])
def emails_scan():
    try:
        if gmail.service is None:
            gmail.authenticate()

        from datetime import timedelta
        ninety_days_ago = (date.today() - timedelta(days=90)).isoformat()
        emails = gmail.fetch_emails(after_date=ninety_days_ago)

        results = []
        receipts_found = 0
        purchases_stored = 0

        for i, email in enumerate(emails):
            subject = email.get("subject", "")
            sender = email.get("from", "")
            body_text = email.get("body_text", "")
            body_html = email.get("body_html", "")

            is_receipt = parser.is_likely_receipt(subject, body_text, body_html, sender)
            skip_reason = None if is_receipt else "no receipt signal"

            entry = {
                "subject": subject,
                "from": sender,
                "date": email.get("date", ""),
                "is_receipt": is_receipt,
                "skip_reason": skip_reason,
                "receipt": None,
            }

            if is_receipt:
                receipt = parser.parse_receipt(subject, sender, body_text, body_html)
                if not receipt:
                    entry["is_receipt"] = False
                    entry["skip_reason"] = "not a purchase"
                else:
                    receipts_found += 1
                    order_number = receipt.get("order_number")
                    if order_number and db.has_purchase_for_order(receipt["retailer"], order_number):
                        entry["is_receipt"] = False
                        entry["skip_reason"] = "duplicate order"
                    else:
                        entry["receipt"] = receipt
                        purchase_id = db.add_purchase(
                            gmail_message_id=email["id"],
                            item_name=receipt["item_name"],
                            price_paid=receipt["price_paid"],
                            product_url=receipt.get("product_url"),
                            retailer=receipt["retailer"],
                            purchase_date=receipt["purchase_date"],
                            currency=receipt.get("currency", "USD"),
                            order_number=order_number,
                            raw_email_snippet=body_text[:500] if body_text else None,
                        )
                        if purchase_id:
                            purchases_stored += 1

            results.append(entry)

        db.set_metadata("last_email_scan_date", date.today().isoformat())

        return jsonify({
            "ok": True,
            "total_emails": len(emails),
            "receipts_found": receipts_found,
            "purchases_stored": purchases_stored,
            "emails": results,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/purchases")
def purchases_list():
    rows = db.get_active_purchases()
    return jsonify({"purchases": rows})


@app.route("/api/policy/discover", methods=["POST"])
def policy_discover():
    try:
        data = request.get_json()
        domain = data.get("domain", "").strip().lower()
        for prefix in ("https://", "http://", "www."):
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
        domain = domain.rstrip("/")
        if not domain:
            return jsonify({"ok": False, "error": "No domain provided"})

        # Check if we already have this retailer in the DB/YAML
        existing = db.get_retailer_by_domain(domain)

        # Try www. first, then bare domain
        html = None
        fetched_url = None
        for prefix in [f"https://www.{domain}", f"https://{domain}"]:
            html = scraper.fetch(prefix)
            if html:
                fetched_url = prefix
                break

        if not html:
            return jsonify({
                "ok": False,
                "error": f"Could not fetch {domain} — site likely has bot protection",
                "existing_retailer": dict(existing) if existing else None,
            })

        footer_links = scraper.extract_footer_links(html, fetched_url)
        policy_links = policy_lookup._find_policy_links(footer_links)

        return jsonify({
            "ok": True,
            "domain": domain,
            "blocked": False,
            "fetched_url": fetched_url,
            "footer_links_total": len(footer_links),
            "policy_links": policy_links,
            "all_footer_links": footer_links,
            "existing_retailer": dict(existing) if existing else None,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


def run():
    init()
    print("\n  Rover Dev Server: http://localhost:5001\n")
    app.run(host="localhost", port=5001, debug=False)


if __name__ == "__main__":
    run()
