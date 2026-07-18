from flask import Blueprint, render_template, request, jsonify, Response
from models import db, Setting, Course, CourseModule, CourseQuiz, CourseAssignment
import json

coursegen_bp = Blueprint("coursegen", __name__, url_prefix="/coursegen")


def get_groq_key():
    s = Setting.query.filter_by(key="GROQ_API_KEY").first()
    return s.value.strip() if s else ""


@coursegen_bp.route("/")
def index():
    courses = Course.query.order_by(Course.created_at.desc()).limit(20).all()
    return render_template("coursegen/index.html", courses=courses)


@coursegen_bp.route("/generate", methods=["POST"])
def generate():
    data = request.get_json()
    topic = data.get("topic", "").strip()
    num_modules = max(2, min(5, int(data.get("num_modules", 3))))
    difficulty = data.get("difficulty", "Beginner")
    language = data.get("language", "English").strip() or "English"

    if not topic:
        return jsonify({"error": "Topic is required."}), 400

    key = get_groq_key()
    if not key:
        return jsonify({"error": "Groq API key not configured. Ask your admin."}), 400

    try:
        from groq import Groq
        client = Groq(api_key=key)

        prompt = (
            f"Create a {difficulty} course on '{topic}' in {language} "
            f"with exactly {num_modules} modules.\n"
            "Return ONLY valid JSON, no text outside it:\n"
            "{\n"
            '  "title": "...",\n'
            '  "description": "2-sentence overview",\n'
            '  "modules": [\n'
            "    {\n"
            '      "number": 1,\n'
            '      "title": "...",\n'
            '      "content": "Detailed markdown (## headings, bullet points, code blocks). Min 200 words.",\n'
            '      "quiz": [\n'
            '        {"question": "...?", "options": ["A","B","C","D"], "correct": 0},\n'
            '        {"question": "...?", "options": ["A","B","C","D"], "correct": 2},\n'
            '        {"question": "...?", "options": ["A","B","C","D"], "correct": 1}\n'
            "      ],\n"
            '      "assignment": {"title": "...", "description": "Min 80 words of instructions."}\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Exactly 3 quiz questions per module, 4 options each, 'correct' is 0-based index."
        )

        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You are an expert course designer. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=4000,
        )

        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())

        course = Course(
            title=parsed["title"],
            topic=topic,
            difficulty=difficulty,
            language=language,
            total_modules=num_modules,
        )
        db.session.add(course)
        db.session.flush()

        for mod_data in parsed.get("modules", []):
            mod = CourseModule(
                course_id=course.id,
                number=mod_data["number"],
                title=mod_data["title"],
                content=mod_data["content"],
            )
            db.session.add(mod)
            db.session.flush()

            for q in mod_data.get("quiz", []):
                db.session.add(CourseQuiz(
                    module_id=mod.id,
                    question=q["question"],
                    options=json.dumps(q["options"]),
                    correct_answer=int(q["correct"]),
                ))

            asgn = mod_data.get("assignment", {})
            if asgn:
                db.session.add(CourseAssignment(
                    module_id=mod.id,
                    title=asgn.get("title", "Assignment"),
                    description=asgn.get("description", ""),
                ))

        db.session.commit()
        return jsonify({"success": True, "id": course.id})

    except json.JSONDecodeError:
        return jsonify({"error": "AI returned an invalid response. Please try again."}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@coursegen_bp.route("/<int:course_id>")
def view(course_id):
    course = Course.query.get_or_404(course_id)
    return render_template("coursegen/view.html", course=course)


@coursegen_bp.route("/<int:course_id>/delete", methods=["POST"])
def delete(course_id):
    course = Course.query.get_or_404(course_id)
    db.session.delete(course)
    db.session.commit()
    return jsonify({"success": True})
