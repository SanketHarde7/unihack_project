import csv

with open("data/Unihack__Expected_Output_-_Delivery_Format.csv", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    rows = list(reader)

print(f"Number of columns: {len(rows[0].keys())}")
print(f"Number of data rows: {len(rows)}")
print()

# Show all non-empty fields for ground truth row 1 (PDSH4816AF)
print("=== PDSH4816AF (Frigidaire) - NON-EMPTY FIELDS ===")
for k, v in rows[0].items():
    if v.strip():
        print(f"  {k}: {v[:120]}")

print(f"\n\n=== WDTS7024RZ (Whirlpool) - NON-EMPTY FIELDS ===")
for k, v in rows[1].items():
    if v.strip():
        print(f"  {k}: {v[:120]}")

# Show all column headers
print(f"\n\n=== ALL COLUMN HEADERS ===")
for i, h in enumerate(rows[0].keys()):
    print(f"  [{i}] {h}")
