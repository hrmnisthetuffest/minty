from minty_core.web.extract import changed_sentences, fingerprint, parse_price, product_from_html

LD = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Boat Rockerz 450",
 "offers":{"@type":"Offer","price":"1499.00","priceCurrency":"INR","availability":"https://schema.org/InStock"}}
</script></head><body>x</body></html>"""


def test_json_ld_product_gives_name_price_currency_and_stock():
    p = product_from_html(LD)
    assert (p.name, p.price, p.currency, p.in_stock) == ("Boat Rockerz 450", 1499.0, "INR", True)


def test_graph_and_offer_lists_take_the_lowest_price_and_out_of_stock_is_false():
    page = """<script type="application/ld+json">{"@graph":[{"@type":"WebSite"},{"@type":"Product","name":"Lamp",
      "offers":[{"price":"900","priceCurrency":"INR","availability":"OutOfStock"},
                {"price":"750","priceCurrency":"INR","availability":"http://schema.org/OutOfStock"}]}]}</script>"""
    p = product_from_html(page)
    assert p.name == "Lamp" and p.price == 750.0 and p.in_stock is False


def test_aggregate_offer_uses_low_price():
    page = ('<script type="application/ld+json">{"@type":"Product","name":"Pen","offers":{"@type":"AggregateOffer",'
            '"lowPrice":"20","highPrice":"30","priceCurrency":"INR"}}</script>')
    assert product_from_html(page).price == 20.0


def test_meta_and_itemprop_are_the_fallbacks():
    assert product_from_html('<meta property="product:price:amount" content="499.00">').price == 499.0
    assert product_from_html('<meta property="og:price:amount" content="1,299">').price == 1299.0
    assert product_from_html('<span itemprop="price" content="12.5">12.50</span>').price == 12.5
    assert product_from_html('<span itemprop="price">₹ 2,499</span>').price == 2499.0


def test_a_page_with_no_price_says_so_and_broken_json_is_ignored():
    p = product_from_html('<script type="application/ld+json">{not json</script><title>Shop</title>')
    assert p.price is None and p.in_stock is None


def test_parse_price_handles_currency_symbols_and_separators():
    assert parse_price("₹1,299.50") == 1299.5
    assert parse_price("Rs. 499") == 499.0
    assert parse_price("$12") == 12.0
    assert parse_price("1.299,50") == 1299.5  # european
    assert parse_price("free") is None


def test_the_fingerprint_ignores_scripts_clocks_and_the_year_but_not_words():
    a = "<html><script>var t=1</script><p>Sale ends soon. © 2026 Shop. 10:42:11</p><p>Batch of 5 pens</p></html>"
    b = "<html><script>var t=999</script><p>Sale ends soon. © 2027 Shop. 11:03:55</p><p>Batch of 5 pens</p></html>"
    c = "<html><p>Sale ends soon. © 2026 Shop. 10:42:11</p><p>Batch of 6 pens</p></html>"
    assert fingerprint(a)[0] == fingerprint(b)[0]
    assert fingerprint(a)[0] != fingerprint(c)[0]


def test_changed_sentences_say_what_was_added_and_removed():
    old = "The hall is open. Entry costs 50 rupees. Bring a pen."
    new = "The hall is open. Entry costs 80 rupees. Bring a pen."
    added, removed = changed_sentences(old, new)
    assert added == ["Entry costs 80 rupees."] and removed == ["Entry costs 50 rupees."]
