from app.services import csv_import

SHOPIFY_CSV = (
    'Handle,Title,Body (HTML),Vendor,Type,Tags,Published,Option1 Name,Option1 Value,Variant SKU,Variant Price,'
    'Variant Inventory Policy,Image Src,Google Shopping / Gender\n'
    'chain-bracelet,7 Shakra Bracelet,"<p>7 chakra bracelet, in <b>blue</b> or black.</p>",Company 123,Bracelet,'
    'Beads,true,Color,Blue,,42.99,deny,https://cdn.example.com/a.jpg,Unisex\n'
    'chain-bracelet,,,,,,,,Black,,42.99,deny,https://cdn.example.com/b.jpg,\n'
    'choker,Choker with Pendant,"<ul>\n<li>Length, 12"" with 2.5"" extender</li>\n<li>Made in USA</li>\n</ul>",'
    'Company 123,Necklace,"Choker, Gold",true,Title,Default Title,CH-1,29.99,deny,https://cdn.example.com/c.jpg,\n'
    ',,,,,,,,,,,,https://cdn.example.com/orphan.jpg,\n'
)


def test_shopify_export_groups_variants_and_falls_back_to_handle():
    parsed = csv_import.parse_csv(SHOPIFY_CSV.encode())
    assert len(parsed.rows) == 4  # the multi-line HTML cell stays one row
    assert csv_import.is_shopify_export(parsed.headers) and csv_import.count_products(parsed) == 2
    mapping = csv_import.suggest_mapping(parsed.headers)
    products, errors = csv_import.build_products(parsed, mapping)
    assert errors == [{"row": 5, "error": "Missing SKU"}] or len(products) == 2
    bracelet, choker = products[0], products[1]
    assert bracelet.sku == "chain-bracelet"  # no Variant SKU: the handle is used
    assert bracelet.images == ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"]
    assert bracelet.attributes == {"Color": "Blue, Black"}  # Shopify bookkeeping columns are dropped
    assert bracelet.original_description == "7 chakra bracelet, in blue or black."
    assert choker.sku == "CH-1" and choker.attributes == {}
    assert choker.original_description == '- Length, 12" with 2.5" extender\n- Made in USA'


def test_shopify_preview_reports_products(client, auth):
    r = client.post("/api/catalogs/preview", headers=auth,
                    files={"file": ("shopify.csv", SHOPIFY_CSV.encode(), "text/csv")}).json()
    assert r["row_count"] == 4 and r["product_count"] == 2 and r["detected_format"] == "shopify"
