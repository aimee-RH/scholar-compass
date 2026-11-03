from flask import render_template

from demo_app import app


def test_demo_chat():
    client = app.test_client()
    assert client.get("/api/health").json["status"] == "healthy"
    home = client.get("/")
    assert home.status_code == 200
    assert b'Fictional data preview' in home.data
    assert b'Find the people behind the ideas.' in home.data
    assert client.get('/style.css').status_code == 200
    with app.test_request_context():
        full_page = render_template('index.html')
        assert 'Fictional data preview' not in full_page
        assert 'Ava Morgan' not in full_page
    first = client.post("/api/chat", json={"message": "trustworthy AI"}).json
    assert first["demo"] is True and "Ava Morgan" in first["message"]
    assert [item["name"] for item in first["advisors"]] == ["Ava Morgan"]
    assert "topics" not in first["advisors"][0]
    follow_up = client.post("/api/chat", json={
        "message": "tell me about the first one",
        "history": [{"role": "assistant", "content": first["message"]}],
    }).json
    assert "Ava Morgan" in follow_up["message"]
    assert follow_up["advisors"][0]["name"] == "Ava Morgan"
    comparison = client.post("/api/chat", json={
        "message": "compare Ava Morgan and Leo Park"
    }).json
    assert "Ava Morgan" in comparison["message"] and "Leo Park" in comparison["message"]
    assert [item["name"] for item in comparison["advisors"]] == ["Ava Morgan", "Leo Park"]
    assert client.post("/api/chat", json={"message": ""}).status_code == 400


if __name__ == "__main__":
    test_demo_chat()
    print("demo checks passed")
