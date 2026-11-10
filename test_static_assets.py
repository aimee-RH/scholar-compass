import csv
from pathlib import Path


ROOT = Path(__file__).parent
NETWORK_DIR = ROOT / "preview-static" / "network"


def test_static_assets():
    home_html = (ROOT / "preview-static" / "index.html").read_text()
    assert 'href="insights/"' in home_html

    insights_html = (ROOT / "preview-static" / "insights" / "index.html").read_text()
    assert "4 interactive visualizations" in insights_html
    assert 'href="../network/"' in insights_html

    venues_html = (ROOT / "preview-static" / "insights" / "venues" / "index.html").read_text()
    assert venues_html.count("<canvas") == 3

    network_html = (NETWORK_DIR / "index.html").read_text()
    assert 'd3.dsv(",", "data.csv"' in network_html

    with (NETWORK_DIR / "data.csv").open(newline="") as source:
        reader = csv.DictReader(source)
        assert {"author1", "author2", "year", "citation"} <= set(reader.fieldnames or [])
        rows = list(reader)

    assert rows
    assert all(row["author1"] and row["author2"] for row in rows)
    assert all(int(row["year"]) > 0 and int(row["citation"]) >= 0 for row in rows)


if __name__ == "__main__":
    test_static_assets()
    print("static asset checks passed")
