"""
Import HackDiet XML backup into hackdiet.db
Usage: python import_xml.py hackdiet_db.xml
"""
import sys
import sqlite3
import xml.etree.ElementTree as ET

ALPHA = 0.1
DB_PATH = "hackdiet.db"


def init_db(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            weight REAL NOT NULL,
            trend REAL
        )
    """)
    db.commit()


def recalculate_trends(db):
    rows = db.execute("SELECT date, weight FROM entries ORDER BY date ASC").fetchall()
    trend = None
    for date, weight in rows:
        if trend is None:
            trend = weight
        else:
            trend = trend + ALPHA * (weight - trend)
        db.execute("UPDATE entries SET trend=? WHERE date=?", (round(trend, 2), date))
    db.commit()


def import_xml(xml_path):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    db = sqlite3.connect(DB_PATH)
    init_db(db)

    count = 0
    for monthlog in root.findall(".//monthlog"):
        props = monthlog.find("properties")
        year = props.findtext("year", "").strip()
        month = props.findtext("month", "").strip()

        for day in monthlog.findall(".//day"):
            day_num = day.findtext("date", "").strip()
            weight_str = day.findtext("weight", "").strip()

            if not weight_str:
                continue

            try:
                weight = float(weight_str)
                date_str = f"{year}-{month}-{int(day_num):02d}"
            except ValueError:
                continue

            db.execute(
                "INSERT INTO entries (date, weight) VALUES (?,?) "
                "ON CONFLICT(date) DO UPDATE SET weight=excluded.weight",
                (date_str, weight)
            )
            count += 1

    db.commit()
    print(f"Imported {count} entries. Recalculating trends...")
    recalculate_trends(db)
    db.close()
    print("Done! Start the app with: python app.py")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python import_xml.py <path_to_hackdiet_db.xml>")
        sys.exit(1)
    import_xml(sys.argv[1])
