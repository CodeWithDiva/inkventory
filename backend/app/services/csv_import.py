"""Catalog CSV parsing, column auto-mapping and row validation."""

import csv
import html
import io
import re
from dataclasses import dataclass, field

# Target fields the app understands; every other column becomes a product attribute.
FIELD_ALIASES: dict[str, list[str]] = {
    "sku": ["sku", "product_id", "productid", "id", "item_id", "code", "product_code", "variant_sku", "article"],
    "name": ["name", "title", "product_name", "productname", "product_title", "item_name"],
    "category": ["category", "categories", "product_type", "type", "department", "collection"],
    "brand": ["brand", "vendor", "manufacturer", "make"],
    "price": ["price", "mrp", "sale_price", "regular_price", "variant_price", "cost"],
    "keywords": ["keywords", "seo_keywords", "tags", "keyword", "focus_keywords"],
    "description": ["description", "body", "body_html", "body (html)", "existing_description", "desc"],
    "images": ["images", "image", "image_url", "image_urls", "image src", "image_src", "main_image", "picture", "photo"],
}
REQUIRED_FIELDS = ("sku", "name")


class CSVImportError(ValueError):
    pass


@dataclass
class ParsedCatalog:
    headers: list[str]
    rows: list[dict[str, str]]


@dataclass
class ProductRow:
    sku: str
    name: str
    category: str | None = None
    brand: str | None = None
    price: str | None = None
    keywords: list[str] = field(default_factory=list)
    original_description: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)


def _normalize(header: str) -> str:
    return re.sub(r"[\s\-]+", "_", header.strip().lower())


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CSVImportError("Could not decode file; please save it as UTF-8 CSV")


def parse_csv(data: bytes, max_rows: int | None = None) -> ParsedCatalog:
    text = _decode(data)
    if not text.strip():
        raise CSVImportError("The file is empty")
    # Only the delimiter is sniffed. Quoting follows Excel rules: the sniffer misreads multi-line
    # quoted cells with doubled quotes (e.g. 12"" in Shopify HTML) and splits them into rows.
    try:
        delimiter = csv.Sniffer().sniff(text[:5000], delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.DictReader(io.StringIO(text), dialect=csv.excel, delimiter=delimiter)
    headers = [h.strip() for h in (reader.fieldnames or []) if h and h.strip()]
    if not headers:
        raise CSVImportError("No header row found")
    rows: list[dict[str, str]] = []
    for row in reader:
        cleaned = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k}
        if any(cleaned.values()):
            rows.append(cleaned)
        if max_rows and len(rows) > max_rows:
            raise CSVImportError(f"File has more than {max_rows} rows; split it into smaller files")
    return ParsedCatalog(headers=headers, rows=rows)


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    """Guess which column holds each target field, e.g. {"sku": "Product ID", "name": "Title"}."""
    normalized = {_normalize(h): h for h in headers}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for target, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            header = normalized.get(_normalize(alias))
            if header and header not in used:
                mapping[target] = header
                used.add(header)
                break
    return mapping


def split_keywords(value: str) -> list[str]:
    parts = re.split(r"[,;|]", value or "")
    seen: list[str] = []
    for part in parts:
        kw = part.strip()
        if kw and kw.lower() not in (s.lower() for s in seen):
            seen.append(kw)
    return seen


def split_image_urls(value: str) -> list[str]:
    urls = [u.strip() for u in re.split(r"[,|;\s]+", value or "") if u.strip()]
    return [u for u in urls if u.startswith(("http://", "https://"))][:10]


def html_to_text(value: str) -> str:
    """Store descriptions as plain text: Shopify / WooCommerce exports contain HTML."""
    value = value.replace("\u2028", "\n").replace("\u2029", "\n")
    if "<" not in value:
        return value
    text = re.sub(r">\s+<", "><", value)  # line breaks between tags are layout, not content
    text = re.sub(r"(?i)</p>|</h[1-6]>|</div>|</ul>|</ol>", "\n\n", text)
    text = re.sub(r"(?i)<br\s*/?>|</li>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n[ \t]*\n(\s*\n)*", "\n\n", text).strip()


# ---------- Shopify product exports ----------
# One product spans several rows: the first has the title, the others carry extra variants
# (option values, SKU, price) or extra images, with Title empty. Rows are grouped by Handle.
SHOPIFY_NOISE = ("handle", "published", "status", "gift card", "variant ", "image ", "seo ", "google shopping",
                 "option1 ", "option2 ", "option3 ", "cost per item", "included /", "price /", "compare at price /")


def is_shopify_export(headers: list[str]) -> bool:
    lower = {h.lower() for h in headers}
    return "handle" in lower and "title" in lower and ("variant sku" in lower or "body (html)" in lower)


def _col(headers: list[str], name: str) -> str | None:
    return next((h for h in headers if h.lower() == name), None)


def count_products(parsed: ParsedCatalog) -> int:
    """How many products the rows describe (Shopify exports use several rows per product)."""
    if not is_shopify_export(parsed.headers):
        return len(parsed.rows)
    handle = _col(parsed.headers, "handle")
    return len({row.get(handle, "") for row in parsed.rows if row.get(handle)})


def merge_shopify_rows(parsed: ParsedCatalog, mapping: dict[str, str]) -> list[tuple[int, dict[str, str]]]:
    """One merged row per Handle: SKU falls back to the handle, images and option values are combined."""
    headers = parsed.headers
    handle_col = _col(headers, "handle")
    groups: dict[str, list[tuple[int, dict[str, str]]]] = {}
    for index, row in enumerate(parsed.rows, start=2):
        groups.setdefault(row.get(handle_col, "") or f"row-{index}", []).append((index, row))

    image_cols = [c for c in (mapping.get("images"), _col(headers, "image src"), _col(headers, "variant image")) if c]
    merged: list[tuple[int, dict[str, str]]] = []
    for handle, group in groups.items():
        index, base = next(((i, r) for i, r in group if r.get(mapping.get("name", ""), "")), group[0])
        row = dict(base)
        sku_col = mapping.get("sku")
        if sku_col and not row.get(sku_col):
            row[sku_col] = next((r[sku_col] for _, r in group if r.get(sku_col)), "") or handle
        if image_cols:
            urls: list[str] = []
            for _, r in group:
                for col in image_cols:
                    for url in split_image_urls(r.get(col, "")):
                        if url not in urls:
                            urls.append(url)
            row[image_cols[0]] = " ".join(urls[:10])
        for n in (1, 2, 3):
            name_col, value_col = _col(headers, f"option{n} name"), _col(headers, f"option{n} value")
            option = (row.get(name_col, "") if name_col else "").strip()
            if not option or option.lower() == "title" or not value_col:
                continue
            values: list[str] = []
            for _, r in group:
                v = r.get(value_col, "").strip()
                if v and v.lower() != "default title" and v not in values:
                    values.append(v)
            if values:
                row[f"__option__{option}"] = ", ".join(values)
        merged.append((index, row))
    return merged


def build_products(
    parsed: ParsedCatalog, mapping: dict[str, str]
) -> tuple[list[ProductRow], list[dict]]:
    """Validate rows against the mapping. Returns (products, errors); bad rows are skipped."""
    missing = [f for f in REQUIRED_FIELDS if not mapping.get(f)]
    if missing:
        raise CSVImportError(f"Column mapping is missing required field(s): {', '.join(missing)}")
    unknown = [col for col in mapping.values() if col and col not in parsed.headers]
    if unknown:
        raise CSVImportError(f"Mapped column(s) not in file: {', '.join(unknown)}")

    mapped_columns = {c for c in mapping.values() if c}
    products: list[ProductRow] = []
    errors: list[dict] = []
    seen_skus: set[str] = set()

    shopify = is_shopify_export(parsed.headers)
    rows = merge_shopify_rows(parsed, mapping) if shopify else list(enumerate(parsed.rows, start=2))  # row 1 = header

    def attributes(row: dict[str, str]) -> dict[str, str]:
        attrs: dict[str, str] = {}
        for k, v in row.items():
            if not v or k in mapped_columns:
                continue
            if k.startswith("__option__"):
                attrs[k[len("__option__"):]] = v
            elif not (shopify and k.lower().startswith(SHOPIFY_NOISE)):
                attrs[k] = v
        return attrs

    for index, row in rows:
        sku = row.get(mapping["sku"], "").strip()
        name = row.get(mapping["name"], "").strip()
        if not sku:
            errors.append({"row": index, "error": "Missing SKU"})
            continue
        if not name:
            errors.append({"row": index, "sku": sku, "error": "Missing product name"})
            continue
        if sku in seen_skus:
            errors.append({"row": index, "sku": sku, "error": "Duplicate SKU in file (row skipped)"})
            continue
        seen_skus.add(sku)

        def get(target: str) -> str | None:
            col = mapping.get(target)
            value = row.get(col, "").strip() if col else ""
            return value or None

        products.append(
            ProductRow(
                sku=sku[:120],
                name=name[:500],
                category=get("category"),
                brand=get("brand"),
                price=get("price"),
                # Shopify tags are labels ("Gold", "Beads"), not search phrases: the product name leads.
                keywords=split_keywords((f"{name.lower()}, " if shopify else "") + (get("keywords") or "")),
                original_description=html_to_text(get("description") or "") or None,
                images=split_image_urls(get("images") or ""),
                attributes=attributes(row),
            )
        )
    return products, errors
