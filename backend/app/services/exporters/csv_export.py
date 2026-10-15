"""CSV exports: a generic file plus files shaped for Shopify's and WooCommerce's CSV importers."""

import csv
import html
import io

from app.models import DescriptionVersion, Product
from app.services.exporters.common import slugify, to_html

CSV_FORMATS = ("csv_generic", "csv_shopify", "csv_woocommerce", "csv_amazon", "csv_ebay", "csv_daraz")

# Marketplace files contain the content columns; merge them into the marketplace's own upload
# template (category templates have many other required columns such as price and stock).


def build_csv(fmt: str, rows: list[tuple[Product, DescriptionVersion]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    if fmt == "csv_shopify":
        writer.writerow(["Handle", "Title", "Body (HTML)", "Variant SKU", "SEO Title", "SEO Description"])
        for product, v in rows:
            writer.writerow([slugify(product.name), v.title, to_html(v), product.sku, v.meta_title, v.meta_description])
    elif fmt == "csv_amazon":
        writer.writerow(["item_sku", "item_name", *(f"bullet_point{i}" for i in range(1, 6)),
                         "product_description", "generic_keywords"])
        for product, v in rows:
            bullets = (list(v.bullet_points) + [""] * 5)[:5]
            # Amazon descriptions are plain text; keywords are space separated, no commas.
            writer.writerow([product.sku, v.title, *bullets, v.long_description.replace("\n\n", " "),
                             " ".join(k.replace(",", " ") for k in (product.keywords or []))])
    elif fmt == "csv_ebay":
        writer.writerow(["Custom label (SKU)", "Title", "Description"])
        for product, v in rows:
            writer.writerow([product.sku, v.title[:80], to_html(v)])
    elif fmt == "csv_daraz":
        writer.writerow(["SellerSku", "name", "short_description", "description"])
        for product, v in rows:
            highlights = "<ul>" + "".join(f"<li>{html.escape(b)}</li>" for b in v.bullet_points) + "</ul>"
            paragraphs = "".join(f"<p>{html.escape(p)}</p>" for p in v.long_description.split("\n\n") if p.strip())
            writer.writerow([product.sku, v.title, highlights, paragraphs])
    elif fmt == "csv_woocommerce":
        writer.writerow(["SKU", "Name", "Short description", "Description"])
        for product, v in rows:
            writer.writerow([product.sku, v.title, v.short_description, to_html(v)])
    else:
        writer.writerow([
            "sku", "language", "title", "short_description", "long_description", "long_description_html",
            "bullet_points", "meta_title", "meta_description", "seo_score", "version",
        ])
        for product, v in rows:
            writer.writerow([
                product.sku, v.language, v.title, v.short_description, v.long_description, to_html(v),
                " | ".join(v.bullet_points), v.meta_title, v.meta_description, v.seo_score, v.version_no,
            ])
    # UTF-8 BOM so Excel opens non-English text correctly.
    return ("﻿" + buffer.getvalue()).encode("utf-8")
