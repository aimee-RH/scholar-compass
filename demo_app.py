"""Small public preview of Scholar Compass using fictional sample profiles."""

import os
import re
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request, send_from_directory


WEB_DIR = Path(__file__).parent / "cse6242_project(frontend)" / "webpage"
app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")

ADVISORS = (
    {
        "name": "Ava Morgan",
        "focus": "Trustworthy AI and human-centered machine learning",
        "topics": {"ai", "machine learning", "trustworthy", "fairness", "explainability", "human"},
        "approach": "Evaluates how people understand and rely on AI systems.",
    },
    {
        "name": "Leo Park",
        "focus": "Computer vision for healthcare",
        "topics": {"computer vision", "vision", "healthcare", "medical", "imaging", "ai"},
        "approach": "Studies visual models for medical images and clinical workflows.",
    },
    {
        "name": "Nina Solis",
        "focus": "Robotics and embodied learning",
        "topics": {"robotics", "robot", "embodied", "reinforcement learning", "control", "ai"},
        "approach": "Builds learning methods for robots that act in changing environments.",
    },
    {
        "name": "Omar Reed",
        "focus": "Graph learning and scientific discovery",
        "topics": {"graph", "knowledge graph", "scientific", "discovery", "network", "machine learning"},
        "approach": "Connects scientific entities with graph models to explore new questions.",
    },
)


def answer(message, history):
    query = message.casefold()
    named = [advisor for advisor in ADVISORS if advisor["name"].casefold() in query]

    if not named and history and re.search(r"\b(first|second|third|fourth)\b", query):
        previous = next((item.get("content", "") for item in reversed(history)
                         if isinstance(item, dict) and item.get("role") == "assistant"), "")
        match = re.search(r"\b(first|second|third|fourth)\b", query)
        index = ("first", "second", "third", "fourth").index(match.group(1))
        listed = re.findall(r"^\d+\. ([A-Z][a-z]+ [A-Z][a-z]+)", previous, re.M)
        if index < len(listed):
            named = [advisor for advisor in ADVISORS if advisor["name"] == listed[index]]

    if len(named) >= 2:
        return "Illustrative comparison (fictional profiles):\n" + "\n".join(
            f"• {advisor['name']} — {advisor['focus']}. {advisor['approach']}"
            for advisor in named[:2]
        ) + "\nChoose based on the research question and methods you want to explore."

    if named:
        advisor = named[0]
        return (f"Illustrative profile — {advisor['name']} (fictional)\n"
                f"Focus: {advisor['focus']}.\nApproach: {advisor['approach']}\n"
                "In the full product, this would link to verified publications and graph evidence.")

    scored = sorted(
        ((sum(topic in query for topic in advisor["topics"]), advisor) for advisor in ADVISORS),
        key=lambda item: item[0], reverse=True,
    )
    matches = [advisor for score, advisor in scored if score > 0][:2]
    if matches:
        return "Illustrative matches (fictional profiles):\n" + "\n".join(
            f"{number}. {advisor['name']} — {advisor['focus']}. {advisor['approach']}"
            for number, advisor in enumerate(matches, 1)
        ) + "\nAsk about a name or say ‘tell me about the first one’."

    return ("Try a research interest such as trustworthy AI, computer vision in healthcare, "
            "robotics, or graph learning. You can also compare Ava Morgan and Leo Park. "
            "All profiles in this preview are fictional.")


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    message = data.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 500:
        return jsonify(success=False, error="Enter a message of 1–500 characters."), 400
    history = data.get("history")
    history = history[-12:] if isinstance(history, list) else []
    session_id = data.get("session_id")
    return jsonify(
        success=True,
        message=answer(message.strip(), history),
        session_id=session_id if isinstance(session_id, str) and session_id else str(uuid4()),
        demo=True,
    )


@app.get("/api/health")
def health():
    return jsonify(status="healthy", mode="fictional_demo", advisors=len(ADVISORS))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5001")), debug=False)
