import csv
import os

FIELD_LABELS = {
    "name": "Business Name",
    "category": "Category",
    "rating": "Rating",
    "review_count": "Review Count",
    "hours": "Hours",
    "address": "Address",
    "website": "Website",
    "phone": "Phone",
    "maps_link": "Maps Link",
    "latitude": "Latitude",
    "longitude": "Longitude",
    "place_id": "Place ID",
    "email": "Email",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "linkedin": "LinkedIn",
    "twitter": "Twitter/X",
    "youtube": "YouTube",
    "review_1": "Review 1",
    "review_2": "Review 2",
    "review_3": "Review 3",
    "domain": "Search Domain",
    "area": "Search Area",
}


def export_csv(
    results: list,
    domain: str,
    area: str,
    fields: list,
    output_path: str = "",
) -> str:
    if output_path and output_path.lower().endswith(".csv"):
        filepath = output_path
    else:
        import re
        output_dir = output_path or "."
        safe_domain = re.sub(r'[^a-zA-Z0-9_\-]', '', domain.strip().replace(" ", "_"))[:50]
        safe_area = re.sub(r'[^a-zA-Z0-9_\-]', '', area.strip().replace(" ", "_"))[:50]
        safe_domain = safe_domain or "domain"
        safe_area = safe_area or "area"
        base_name = f"{safe_domain}_in_{safe_area}"
        filename = f"{base_name}.csv"
        filepath = os.path.join(output_dir, filename)
        suffix = 1
        while os.path.exists(filepath):
            filename = f"{base_name}_{suffix}.csv"
            filepath = os.path.join(output_dir, filename)
            suffix += 1

    headers = [FIELD_LABELS[f] for f in fields if f in FIELD_LABELS]

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow({
                FIELD_LABELS[f]: row.get(f, "")
                for f in fields
                if f in FIELD_LABELS
            })

    return filepath
