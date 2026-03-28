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
from rover.price_checker import PriceChecker
from rover.scraper import Scraper

app = Flask(__name__)

# --- Globals initialized on startup ---
config = None
db = None
gmail = None
parser = None
scraper = None
policy_lookup = None
price_checker = None


def init():
    global config, db, gmail, parser, scraper, policy_lookup, price_checker
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
    price_checker = PriceChecker(
        config=config,
        db=db,
        scraper=scraper,
        policy_lookup=policy_lookup,
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

<!-- URL Discovery -->
<div class="section">
  <h2>4. Discover Product URLs</h2>
  <p style="font-size:0.85rem;color:#888;margin-bottom:12px;">Searches DuckDuckGo to find product page URLs for purchases that don't have one.</p>
  <button onclick="discoverUrls()" id="btn-urls">Find Product URLs</button>
  <div class="status" id="urls-status"></div>
  <div class="results" id="urls-results"></div>
</div>

<!-- Price Check -->
<div class="section">
  <h2>5. Check Prices</h2>
  <p style="font-size:0.85rem;color:#888;margin-bottom:12px;">Scrapes current prices for tracked purchases and detects price drops within refund windows.</p>
  <button onclick="checkPrices()" id="btn-prices">Check Prices</button>
  <div class="status" id="prices-status"></div>
  <div class="results" id="prices-results"></div>
</div>

<!-- Policy Discovery -->
<div class="section">
  <h2>6. Discover Refund Policies</h2>
  <p style="font-size:0.85rem;color:#888;margin-bottom:12px;">Searches for return/refund policies for all retailers in your purchases. Uses regex to extract refund windows.</p>
  <button onclick="discoverPolicies()" id="btn-policies">Find Refund Policies</button>
  <div class="status" id="policies-status"></div>
  <div class="results" id="policies-results"></div>
</div>

<!-- Manual Policy Lookup -->
<div class="section">
  <h2>7. Manual Policy Lookup</h2>
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
        const items = Array.isArray(e.receipt) ? e.receipt : [e.receipt];
        for (const r of items) {
          html += '<div class="row">';
          html += '<div><div class="label">Item</div><div class="value">' + esc(r.item_name) + '</div></div>';
          html += '<div><div class="label">Price</div><div class="value">$' + r.price_paid.toFixed(2) + '</div></div>';
          html += '<div><div class="label">Retailer</div><div class="value">' + esc(r.retailer) + '</div></div>';
          html += '</div>';
          if (r.product_url) {
            html += '<div><div class="label">Product URL</div><div class="value"><a href="' + esc(r.product_url) + '" target="_blank">' + esc(r.product_url) + '</a></div></div>';
          }
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

async function discoverUrls() {
  const btn = document.getElementById('btn-urls');
  btn.disabled = true;
  setStatus('urls-status', '<span class="spinner"></span> Searching for product URLs (this may take a while)...');
  document.getElementById('urls-results').innerHTML = '';
  try {
    const res = await fetch('/api/purchases/discover-urls', {method: 'POST'});
    const data = await res.json();
    if (!data.ok) { setStatus('urls-status', 'Error: ' + data.error, 'err'); btn.disabled = false; return; }
    setStatus('urls-status', data.urls_found + ' new URLs found', 'ok');
    const container = document.getElementById('urls-results');
    let html = '';
    for (const p of data.purchases) {
      const hasUrl = !!p.product_url;
      const tag = hasUrl ? '<span class="tag receipt">has URL</span>' : '<span class="tag skip">no URL</span>';
      html += '<div class="card"><div class="row">';
      html += '<div><div class="label">Item</div><div class="value">' + esc(p.item_name) + ' ' + tag + '</div></div>';
      html += '<div><div class="label">Retailer</div><div class="value">' + esc(p.retailer) + '</div></div>';
      html += '</div>';
      if (p.product_url) {
        html += '<div><div class="label">URL</div><div class="value"><a href="' + esc(p.product_url) + '" target="_blank" style="color:#60a5fa;">' + esc(p.product_url).substring(0, 80) + '</a></div></div>';
      }
      html += '</div>';
    }
    container.innerHTML = html;
  } catch(e) {
    setStatus('urls-status', 'Request failed: ' + e, 'err');
  }
  btn.disabled = false;
}

async function checkPrices() {
  const btn = document.getElementById('btn-prices');
  btn.disabled = true;
  setStatus('prices-status', '<span class="spinner"></span> Checking prices (scraping product pages)...');
  document.getElementById('prices-results').innerHTML = '';
  try {
    const res = await fetch('/api/purchases/check-prices', {method: 'POST'});
    const data = await res.json();
    if (!data.ok) { setStatus('prices-status', 'Error: ' + data.error, 'err'); btn.disabled = false; return; }
    setStatus('prices-status', data.total_drops + ' price drop(s) detected', data.total_drops > 0 ? 'ok' : '');
    const container = document.getElementById('prices-results');
    let html = '';
    if (data.drops.length) {
      for (const d of data.drops) {
        html += '<div class="card" style="border-color:#22c55e;">';
        html += '<div class="row">';
        html += '<div><div class="label">Item</div><div class="value">' + esc(d.item_name) + ' <span class="tag receipt">PRICE DROP</span></div></div>';
        html += '</div><div class="row">';
        html += '<div><div class="label">Paid</div><div class="value">$' + d.price_paid.toFixed(2) + '</div></div>';
        html += '<div><div class="label">Now</div><div class="value" style="color:#22c55e;">$' + d.current_price.toFixed(2) + '</div></div>';
        html += '<div><div class="label">Savings</div><div class="value" style="color:#22c55e;font-weight:bold;">$' + d.savings_amount.toFixed(2) + '</div></div>';
        html += '</div></div>';
      }
    } else {
      html += '<div style="color:#666;font-size:0.85rem;margin-bottom:12px;">No price drops detected.</div>';
    }
    if (data.all_checks && data.all_checks.length) {
      html += '<div style="margin-top:16px;"><div class="label" style="margin-bottom:8px;">All Price Checks</div>';
      for (const c of data.all_checks) {
        const hasPrice = c.current_price != null;
        let detail = '';
        if (hasPrice) {
          const diff = c.price_paid - c.current_price;
          if (diff > 0) detail = '<span style="color:#22c55e;">now $' + c.current_price.toFixed(2) + ' (save $' + diff.toFixed(2) + ')</span>';
          else if (diff === 0) detail = '<span style="color:#888;">$' + c.current_price.toFixed(2) + ' (no change)</span>';
          else detail = '<span style="color:#ef4444;">now $' + c.current_price.toFixed(2) + ' (up $' + Math.abs(diff).toFixed(2) + ')</span>';
        } else {
          detail = '<span class="tag skip">' + esc(c.status) + '</span>';
        }
        html += '<div class="card" style="padding:10px;"><div class="row">';
        html += '<div style="flex:2;"><div class="value" style="font-size:0.85rem;">' + esc(c.item_name) + '</div></div>';
        html += '<div><div class="value" style="font-size:0.85rem;">paid $' + c.price_paid.toFixed(2) + '</div></div>';
        html += '<div style="flex:1.5;"><div class="value" style="font-size:0.85rem;">' + detail + '</div></div>';
        html += '</div></div>';
      }
      html += '</div>';
    }
    container.innerHTML = html;
  } catch(e) {
    setStatus('prices-status', 'Request failed: ' + e, 'err');
  }
  btn.disabled = false;
}

async function discoverPolicies() {
  const btn = document.getElementById('btn-policies');
  btn.disabled = true;
  setStatus('policies-status', '<span class="spinner"></span> Searching for refund policies...');
  document.getElementById('policies-results').innerHTML = '';
  try {
    const res = await fetch('/api/policies/discover', {method: 'POST'});
    const data = await res.json();
    if (!data.ok) { setStatus('policies-status', 'Error: ' + data.error, 'err'); btn.disabled = false; return; }
    const discovered = data.policies.filter(p => p.status === 'discovered').length;
    setStatus('policies-status', `${data.policies.length} retailers checked, ${discovered} newly discovered`, 'ok');
    const container = document.getElementById('policies-results');
    let html = '';
    for (const p of data.policies) {
      const windowType = p.window_type === 'price_adjustment' ? 'price adjustment' : p.window_type === 'return' ? 'return window' : 'default';
      const sourceTag = p.source === 'scraped' ? '<span class="tag receipt">scraped</span>'
        : p.source === 'manual' ? '<span class="tag skip">from config</span>'
        : '<span class="tag skip">default</span>';
      html += '<div class="card" style="padding:12px;"><div class="row">';
      html += '<div style="flex:2;"><div class="label">Retailer</div><div class="value">' + esc(p.retailer) + ' ' + sourceTag + '</div></div>';
      html += '<div><div class="label">Refund Window</div><div class="value">' + p.refund_window_days + ' days (' + windowType + ')</div></div>';
      html += '</div>';
      if (p.support_email || p.policy_url) {
        html += '<div class="row" style="margin-top:4px;">';
        if (p.support_email) html += '<div><div class="label">Contact</div><div class="value">' + esc(p.support_email) + '</div></div>';
        if (p.policy_url) html += '<div><div class="label">Policy</div><div class="value"><a href="' + esc(p.policy_url) + '" target="_blank" style="color:#60a5fa;">' + esc(p.policy_url).substring(0, 60) + '</a></div></div>';
        html += '</div>';
      }
      html += '</div>';
    }
    container.innerHTML = html;
  } catch(e) {
    setStatus('policies-status', 'Request failed: ' + e, 'err');
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
                items = parser.parse_receipt(subject, sender, body_text, body_html, email_date=email.get("date", ""))
                if not items:
                    entry["is_receipt"] = False
                    entry["skip_reason"] = "not a purchase"
                else:
                    receipts_found += 1
                    order_number = items[0].get("order_number")
                    entry["receipt"] = items
                    all_dupes = True
                    for idx, item in enumerate(items):
                        if order_number and db.has_purchase_for_item(
                            item["retailer"], order_number, item["item_name"]
                        ):
                            continue
                        all_dupes = False
                        item_msg_id = f"{email['id']}:{idx}" if len(items) > 1 else email["id"]
                        purchase_id = db.add_purchase(
                            gmail_message_id=item_msg_id,
                            item_name=item["item_name"],
                            price_paid=item["price_paid"],
                            product_url=item.get("product_url"),
                            retailer=item["retailer"],
                            purchase_date=item["purchase_date"],
                            currency=item.get("currency", "USD"),
                            order_number=order_number,
                            raw_email_snippet=body_text[:500] if body_text else None,
                        )
                        if purchase_id:
                            purchases_stored += 1
                    if all_dupes:
                        entry["is_receipt"] = False
                        entry["skip_reason"] = "duplicate order"
                        entry["receipt"] = None

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


@app.route("/api/policies/discover", methods=["POST"])
def discover_policies():
    try:
        # Get unique retailer domains from purchases
        purchases = db.get_active_purchases()
        retailers_seen = set()
        results = []

        for p in purchases:
            retailer = p.get("retailer", "")
            if retailer in retailers_seen:
                continue
            retailers_seen.add(retailer)

            # Check if we already have a scraped policy
            existing = None
            rows = db.conn.execute(
                "SELECT * FROM retailers WHERE name = ? OR domain LIKE ?",
                (retailer, f"%{retailer.lower().split()[0]}%"),
            ).fetchall()
            if rows:
                existing = dict(rows[0])

            domain = existing.get("domain") if existing else None
            if not domain:
                # Try to find domain from product_url
                product_url = p.get("product_url")
                if product_url:
                    from urllib.parse import urlparse as _urlparse
                    d = _urlparse(product_url).netloc.lower()
                    if d.startswith("www."):
                        d = d[4:]
                    domain = d

            if existing and existing.get("source") == "scraped":
                results.append({
                    "retailer": retailer,
                    "domain": domain,
                    "refund_window_days": existing["refund_window_days"],
                    "support_email": existing.get("support_email"),
                    "policy_url": existing.get("policy_url"),
                    "source": existing["source"],
                    "status": "already_scraped",
                })
                continue

            if not domain:
                results.append({
                    "retailer": retailer,
                    "domain": None,
                    "refund_window_days": existing["refund_window_days"] if existing else 30,
                    "source": existing["source"] if existing else "default",
                    "status": "no_domain",
                })
                continue

            info = policy_lookup.discover_policy(domain, retailer_name=retailer)
            # If scraping failed (returned default), prefer existing DB data
            if info.source == "default" and existing:
                results.append({
                    "retailer": retailer,
                    "domain": domain,
                    "refund_window_days": existing["refund_window_days"],
                    "support_email": existing.get("support_email"),
                    "policy_url": existing.get("policy_url"),
                    "source": existing.get("source", "manual"),
                    "window_type": existing.get("window_type", "return"),
                    "status": "manual",
                })
            else:
                results.append({
                    "retailer": retailer,
                    "domain": domain,
                    "refund_window_days": info.refund_window_days,
                    "support_email": info.support_email,
                    "policy_url": info.policy_url,
                    "source": info.source,
                    "window_type": info.window_type,
                    "status": "discovered" if info.source == "scraped" else info.source,
                })

        return jsonify({"ok": True, "policies": results})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/purchases/discover-urls", methods=["POST"])
def discover_urls():
    try:
        found = price_checker.discover_product_urls()
        purchases = db.get_active_purchases()
        return jsonify({
            "ok": True,
            "urls_found": found,
            "purchases": [dict(p) for p in purchases],
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/purchases/check-prices", methods=["POST"])
def check_prices():
    try:
        drops = price_checker.check_all_prices()

        # Also return all recent checks for the UI
        checks = db.conn.execute("""
            SELECT p.item_name, p.price_paid, p.retailer, pc.current_price, pc.status
            FROM price_checks pc
            JOIN purchases p ON p.id = pc.purchase_id
            ORDER BY pc.checked_at DESC
        """).fetchall()
        all_checks = [
            {
                "item_name": c[0], "price_paid": c[1], "retailer": c[2],
                "current_price": c[3], "status": c[4],
            }
            for c in checks
        ]

        return jsonify({
            "ok": True,
            "drops": drops,
            "total_drops": len(drops),
            "all_checks": all_checks,
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
