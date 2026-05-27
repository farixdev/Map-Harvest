import csv
import os

FIELD_LABELS = {
    "name": "Business Name",
    "rating": "Rating",
    "address": "Address",
    "website": "Website",
    "phone": "Phone",
    "maps_link": "Maps Link",
}


def export_csv(results: list, domain: str, area: str, fields: list, output_dir: str = ".") -> str:
    safe_domain = domain.strip().lower().replace(" ", "_")
    safe_area = area.strip().lower().replace(" ", "_")
    filename = f"{safe_domain}_in_{safe_area}.csv"
    filepath = os.path.join(output_dir, filename)

    headers = [FIELD_LABELS[f] for f in fields if f in FIELD_LABELS]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in results:
            writer.writerow({
                FIELD_LABELS[f]: row.get(f, "")
                for f in fields
                if f in FIELD_LABELS
            })

    return filepath

