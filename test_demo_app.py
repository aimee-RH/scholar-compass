from flask import render_template

from demo_app import app


def test_demo_chat():
    client = app.test_client()
    assert client.get("/api/health").json["status"] == "healthy"
    home = client.get("/")
    assert home.status_code == 200
    assert b'INTERACTIVE PREVIEW' in home.data
    with app.test_request_context():
        assert 'INTERACTIVE PREVIEW' not in render_template('index.html')
    first = client.post("/api/chat", json={"message": "trustworthy AI"}).json
    assert first["demo"] is True and "Ava Morgan" in first["message"]
    follow_up = client.post("/api/chat", json={
        "message": "tell me about the first one",
        "history": [{"role": "assistant", "content": first["message"]}],
    }).json
    assert "Ava Morgan" in follow_up["message"]
    comparison = client.post("/api/chat", json={
        "message": "compare Ava Morgan and Leo Park"
    }).json
    assert "Ava Morgan" in comparison["message"] and "Leo Park" in comparison["message"]
    assert client.post("/api/chat", json={"message": ""}).status_code == 400


if __name__ == "__main__":
    test_demo_chat()
    print("demo checks passed")
