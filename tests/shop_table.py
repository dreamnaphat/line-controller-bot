"""The production "Shop" table, for tests that read it."""

# Same definition as the production "Shop" table.
SHOP_DDL = """
CREATE TABLE "Shop" (
    "placeId" text PRIMARY KEY,
    "name" text NOT NULL,
    "estimatedOpeningDate" text,
    "calculatedAgeDays" integer,
    "analysisReason" text NOT NULL,
    "rating" double precision,
    "userRatingCount" integer NOT NULL,
    "phoneNumber" text,
    "latitude" double precision,
    "longitude" double precision,
    "googleMapsUrl" text NOT NULL,
    "province" text NOT NULL,
    "district" text NOT NULL,
    "foodType" text NOT NULL,
    "firstFoundAt" timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "lastSeenAt" timestamp NOT NULL,
    "lastScanId" text NOT NULL,
    "status" text DEFAULT 'none' NOT NULL,
    "statusChangedAt" timestamp,
    "visited" boolean DEFAULT false NOT NULL,
    "visitedAt" timestamp,
    "visitNote" text,
    "saleId" integer,
    "saleName" text,
    "foundOnPage" integer
)
"""


def create_shop_table(conn) -> None:
    conn.execute('DROP TABLE IF EXISTS "Shop"')
    conn.execute(SHOP_DDL)


def add_shop(conn, place_id, phone, age=None, status="none", visited=False, food="ก๋วยเตี๋ยว"):
    conn.execute(
        'INSERT INTO "Shop" ("placeId", "name", "calculatedAgeDays", "analysisReason", "userRatingCount",'
        ' "phoneNumber", "googleMapsUrl", "province", "district", "foodType", "lastSeenAt", "lastScanId",'
        ' "status", "visited")'
        " VALUES (%s, %s, %s, 'new', 3, %s, 'https://maps.google.com', 'กรุงเทพ', 'บางนา', %s, now(), 'scan-1', %s, %s)",
        (place_id, f"ร้าน {place_id}", age, phone, food, status, visited),
    )
