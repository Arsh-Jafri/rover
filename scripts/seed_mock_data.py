"""
Seed mock data for the Rover dashboard.
Run: python scripts/seed_mock_data.py

Inserts realistic purchases, price checks, savings, activity, and notifications
for the first user found in the database.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()

import psycopg2
import psycopg2.extras


def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set in .env")
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Ensure notifications table exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id SERIAL PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            body TEXT,
            link TEXT,
            read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    # Get the first user
    cur.execute("SELECT id, email, name FROM users ORDER BY created_at LIMIT 1")
    user = cur.fetchone()
    if not user:
        print("No users found. Sign up first, then run this script.")
        sys.exit(1)

    user_id = str(user["id"])
    print(f"Seeding data for user: {user['email']} ({user_id})")

    # Clean existing mock data
    cur.execute("DELETE FROM notifications WHERE user_id = %s", (user_id,))
    cur.execute("""
        DELETE FROM savings WHERE purchase_id IN (SELECT id FROM purchases WHERE user_id = %s)
    """, (user_id,))
    cur.execute("""
        DELETE FROM price_checks WHERE purchase_id IN (SELECT id FROM purchases WHERE user_id = %s)
    """, (user_id,))
    cur.execute("DELETE FROM purchases WHERE user_id = %s", (user_id,))
    print("Cleared existing data.")

    # ------------------------------------------------------------------
    # Purchases — realistic items from various retailers
    # ------------------------------------------------------------------
    purchases = [
        {
            "item_name": "Nike Air Max 90",
            "price_paid": 129.99,
            "retailer": "Nike",
            "purchase_date": "2026-03-15",
            "order_number": "C10298345",
            "product_url": "https://www.nike.com/t/air-max-90-mens-shoes-6n3vKB",
            "currency": "USD",
            "gmail_message_id": "mock_nike_001",
        },
        {
            "item_name": "Sony WH-1000XM5 Headphones",
            "price_paid": 349.99,
            "retailer": "Amazon",
            "purchase_date": "2026-03-12",
            "order_number": "112-4839201-7723456",
            "product_url": "https://www.amazon.com/dp/B0BX2L8PCP",
            "currency": "USD",
            "gmail_message_id": "mock_amazon_001",
        },
        {
            "item_name": "Patagonia Better Sweater Jacket",
            "price_paid": 149.00,
            "retailer": "Patagonia",
            "purchase_date": "2026-03-10",
            "order_number": "PAT-8834210",
            "product_url": "https://www.patagonia.com/product/mens-better-sweater-fleece-jacket/25528.html",
            "currency": "USD",
            "gmail_message_id": "mock_patagonia_001",
        },
        {
            "item_name": "Apple AirPods Pro 2",
            "price_paid": 249.00,
            "retailer": "Apple",
            "purchase_date": "2026-02-05",
            "order_number": "W924830129",
            "product_url": "https://www.apple.com/shop/product/MTJV3AM/A",
            "currency": "USD",
            "gmail_message_id": "mock_apple_001",
        },
        {
            "item_name": "Lululemon ABC Jogger",
            "price_paid": 128.00,
            "retailer": "Lululemon",
            "purchase_date": "2026-03-18",
            "order_number": "LUL-9921034",
            "product_url": "https://shop.lululemon.com/p/men-joggers/Abc-Jogger/_/prod8530241",
            "currency": "USD",
            "gmail_message_id": "mock_lulu_001",
        },
        {
            "item_name": "New Balance 990v6",
            "price_paid": 199.99,
            "retailer": "New Balance",
            "purchase_date": "2026-03-20",
            "order_number": "NB-3847291",
            "product_url": "https://www.newbalance.com/pd/made-in-usa-990v6/M990V6-44457.html",
            "currency": "USD",
            "gmail_message_id": "mock_nb_001",
        },
        {
            "item_name": "North Face Nuptse Jacket",
            "price_paid": 320.00,
            "retailer": "The North Face",
            "purchase_date": "2026-03-05",
            "order_number": "TNF-1102847",
            "product_url": "https://www.thenorthface.com/en-us/mens/mens-jackets-and-vests/mens-insulated-jackets-c220150",
            "currency": "USD",
            "gmail_message_id": "mock_tnf_001",
        },
        {
            "item_name": "Aesop Resurrection Hand Balm",
            "price_paid": 39.00,
            "retailer": "Aesop",
            "purchase_date": "2026-01-15",
            "order_number": "AES-774321",
            "product_url": "https://www.aesop.com/us/p/body-hand/hand/resurrection-aromatique-hand-balm/",
            "currency": "USD",
            "gmail_message_id": "mock_aesop_001",
        },
        {
            "item_name": "Uniqlo Ultra Light Down Jacket",
            "price_paid": 79.90,
            "retailer": "Uniqlo",
            "purchase_date": "2026-03-14",
            "order_number": "UQ-20263140088",
            "product_url": "https://www.uniqlo.com/us/en/products/E462586-000",
            "currency": "USD",
            "gmail_message_id": "mock_uniqlo_001",
        },
        {
            "item_name": "Kindle Paperwhite Signature Edition",
            "price_paid": 189.99,
            "retailer": "Amazon",
            "purchase_date": "2026-02-10",
            "order_number": "112-9928374-1102938",
            "product_url": "https://www.amazon.com/dp/B0CFPJYX7P",
            "currency": "USD",
            "gmail_message_id": "mock_amazon_002",
        },
        {
            "item_name": "Allbirds Tree Runners",
            "price_paid": 98.00,
            "retailer": "Allbirds",
            "purchase_date": "2026-03-25",
            "order_number": "AB-663920",
            "product_url": "https://www.allbirds.com/products/mens-tree-runners",
            "currency": "USD",
            "gmail_message_id": "mock_allbirds_001",
        },
        {
            "item_name": "Stanley Quencher H2.0 Tumbler",
            "price_paid": 45.00,
            "retailer": "Stanley",
            "purchase_date": "2026-03-24",
            "order_number": "STN-88291",
            "product_url": "https://www.stanley1913.com/products/quencher-h2-0-flowstate-tumbler-40-oz",
            "currency": "USD",
            "gmail_message_id": "mock_stanley_001",
        },
    ]

    purchase_ids = []
    for p in purchases:
        cur.execute(
            """INSERT INTO purchases (user_id, gmail_message_id, item_name, price_paid,
               product_url, retailer, purchase_date, currency, order_number, url_search_attempted)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
               RETURNING id""",
            (user_id, p["gmail_message_id"], p["item_name"], p["price_paid"],
             p["product_url"], p["retailer"], p["purchase_date"], p["currency"],
             p["order_number"]),
        )
        pid = cur.fetchone()["id"]
        purchase_ids.append(pid)

    print(f"Inserted {len(purchases)} purchases.")

    # ------------------------------------------------------------------
    # Price checks — most succeed, a couple fail
    # ------------------------------------------------------------------
    price_check_data = [
        # (purchase_index, current_price, status, checked_at, error_detail)
        (0, 109.99, "success", "2026-03-27 14:00:00+00", None),        # Nike — dropped!
        (1, 349.99, "success", "2026-03-27 14:05:00+00", None),        # Sony — same
        (2, 119.00, "success", "2026-03-27 14:10:00+00", None),        # Patagonia — dropped!
        (3, 249.00, "success", "2026-03-27 14:15:00+00", None),        # Apple — same
        (4, 118.00, "success", "2026-03-27 14:20:00+00", None),        # Lulu — dropped!
        (5, 199.99, "success", "2026-03-27 14:25:00+00", None),        # NB — same
        (6, 279.99, "success", "2026-03-27 14:30:00+00", None),        # TNF — dropped!
        (7, 39.00, "success", "2026-03-27 14:35:00+00", None),         # Aesop — same
        (8, None, "scrape_failed", "2026-03-27 14:40:00+00", "Bot protection blocked request"),  # Uniqlo — failed
        (9, 189.99, "success", "2026-03-27 14:45:00+00", None),        # Kindle — same
        (10, 98.00, "success", "2026-03-27 14:50:00+00", None),        # Allbirds — same
        (11, 35.00, "success", "2026-03-27 14:55:00+00", None),        # Stanley — dropped!
        # Older checks
        (0, 129.99, "success", "2026-03-21 10:00:00+00", None),
        (1, 319.99, "success", "2026-03-21 10:05:00+00", None),        # Sony was cheaper earlier
        (2, 149.00, "success", "2026-03-21 10:10:00+00", None),
        (6, 320.00, "success", "2026-03-21 10:15:00+00", None),
    ]

    price_check_ids = {}
    for idx, (pi, price, status, checked_at, error) in enumerate(price_check_data):
        cur.execute(
            """INSERT INTO price_checks (purchase_id, current_price, checked_at, status, error_detail)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (purchase_ids[pi], price, checked_at, status, error),
        )
        pcid = cur.fetchone()["id"]
        price_check_ids[idx] = pcid

    print(f"Inserted {len(price_check_data)} price checks.")

    # ------------------------------------------------------------------
    # Savings — items that dropped in price
    # ------------------------------------------------------------------
    savings_data = [
        # (purchase_index, pc_index, original, dropped, savings_amount, status, detected_at)
        (0, 0, 129.99, 109.99, 20.00, "notified", "2026-03-27 14:01:00+00"),   # Nike
        (2, 2, 149.00, 119.00, 30.00, "claimed",  "2026-03-27 14:11:00+00"),    # Patagonia
        (4, 4, 128.00, 118.00, 10.00, "new",      "2026-03-27 14:21:00+00"),    # Lulu
        (6, 6, 320.00, 279.99, 40.01, "notified", "2026-03-27 14:31:00+00"),    # TNF
        (11, 11, 45.00, 35.00, 10.00, "new",      "2026-03-27 14:56:00+00"),    # Stanley
    ]

    for pi, pci, orig, dropped, amount, status, detected in savings_data:
        cur.execute(
            """INSERT INTO savings (purchase_id, price_check_id, original_price, dropped_price,
               savings_amount, detected_at, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (purchase_ids[pi], price_check_ids[pci], orig, dropped, amount, detected, status),
        )

    print(f"Inserted {len(savings_data)} savings.")

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    notifications = [
        ("Price drop on Nike Air Max 90", "Rover spotted a $20.00 drop — now $109.99. You're within the 30-day return window.", None, False, "2026-03-27 14:02:00+00"),
        ("Price drop on North Face Nuptse Jacket", "Rover found a $40.01 price drop on your Nuptse Jacket. Claim it before it goes back up.", None, False, "2026-03-27 14:32:00+00"),
        ("Patagonia claim successful", "You saved $30.00 on your Better Sweater Jacket. Patagonia confirmed the price adjustment.", None, True, "2026-03-27 16:00:00+00"),
        ("Price drop on Lululemon ABC Jogger", "Down $10.00 to $118.00. Rover's keeping an eye on it.", None, False, "2026-03-27 14:22:00+00"),
        ("Price drop on Stanley Quencher", "Your tumbler dropped to $35.00 — that's $10 back in your pocket.", None, False, "2026-03-27 14:57:00+00"),
        ("Welcome to Rover", "Rover's connected and watching your inbox. He'll catch every price drop.", None, True, "2026-03-15 09:00:00+00"),
        ("Gmail connected", "Rover scanned your inbox and found 12 recent purchases to track.", None, True, "2026-03-15 09:01:00+00"),
        ("First scan complete", "Rover found product URLs for 10 out of 12 items. Price tracking begins now.", None, True, "2026-03-15 09:30:00+00"),
    ]

    for title, body, link, read, created_at in notifications:
        cur.execute(
            """INSERT INTO notifications (user_id, title, body, link, read, created_at)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (user_id, title, body, link, read, created_at),
        )

    print(f"Inserted {len(notifications)} notifications.")

    # ------------------------------------------------------------------
    # Retailers
    # ------------------------------------------------------------------
    retailers = [
        ("Nike", "nike.com", 30, "support@nike.com", "https://www.nike.com/help", "https://www.nike.com/help/a/return-policy"),
        ("Amazon", "amazon.com", 30, None, "https://www.amazon.com/gp/help/customer/contact-us", "https://www.amazon.com/gp/help/customer/display.html?nodeId=GKM69DUUYKQWKCES"),
        ("Patagonia", "patagonia.com", 30, "customer_service@patagonia.com", "https://www.patagonia.com/returns.html", "https://www.patagonia.com/returns.html"),
        ("Apple", "apple.com", 14, None, "https://getsupport.apple.com", "https://www.apple.com/shop/help/returns_refund"),
        ("Lululemon", "lululemon.com", 30, "gec@lululemon.com", "https://shop.lululemon.com/help", "https://info.lululemon.com/help/our-policies/return-policy"),
        ("New Balance", "newbalance.com", 45, None, "https://www.newbalance.com/customer-care/", "https://www.newbalance.com/returns-and-exchanges.html"),
        ("The North Face", "thenorthface.com", 60, None, "https://www.thenorthface.com/help.html", "https://www.thenorthface.com/help/returns-and-warranty.html"),
        ("Aesop", "aesop.com", 60, None, "https://www.aesop.com/us/r/contact-us", "https://www.aesop.com/us/r/shipping-and-returns"),
        ("Uniqlo", "uniqlo.com", 30, None, "https://www.uniqlo.com/us/en/help/contact-us.html", "https://www.uniqlo.com/us/en/help/returns-and-exchanges.html"),
        ("Allbirds", "allbirds.com", 30, "help@allbirds.com", "https://www.allbirds.com/pages/contact", "https://www.allbirds.com/pages/return-policy"),
        ("Stanley", "stanley1913.com", 30, None, "https://www.stanley1913.com/pages/contact-us", "https://www.stanley1913.com/policies/refund-policy"),
    ]

    for name, domain, window, email, support, policy in retailers:
        cur.execute(
            """INSERT INTO retailers (name, domain, refund_window_days, support_email, support_url, policy_url, source)
               VALUES (%s, %s, %s, %s, %s, %s, 'manual')
               ON CONFLICT (domain) DO UPDATE SET
                   refund_window_days = EXCLUDED.refund_window_days,
                   support_email = EXCLUDED.support_email,
                   support_url = EXCLUDED.support_url,
                   policy_url = EXCLUDED.policy_url""",
            (name, domain, window, email, support, policy),
        )

    print(f"Upserted {len(retailers)} retailers.")

    cur.close()
    conn.close()
    print("\nDone! Refresh your dashboard to see the mock data.")


if __name__ == "__main__":
    main()
