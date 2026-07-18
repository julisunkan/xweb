from flask import Blueprint, render_template, request, jsonify, Response
from models import db, Setting, Newsletter
import json

emailnewsgen_bp = Blueprint("emailnewsgen", __name__, url_prefix="/emailnewsgen")


def get_groq_key():
    s = Setting.query.filter_by(key="GROQ_API_KEY").first()
    return s.value.strip() if s else ""


@emailnewsgen_bp.route("/")
def index():
    newsletters = Newsletter.query.order_by(Newsletter.created_at.desc()).limit(20).all()
    return render_template("emailnewsgen/index.html", newsletters=newsletters)


@emailnewsgen_bp.route("/generate", methods=["POST"])
def generate():
    data = request.get_json()
    topic = data.get("topic", "").strip()
    audience = data.get("audience", "General audience").strip() or "General audience"
    tone = data.get("tone", "Professional")
    num_sections = max(2, min(5, int(data.get("num_sections", 3))))
    cta_text = data.get("cta_text", "Learn More").strip() or "Learn More"

    if not topic:
        return jsonify({"error": "Topic is required."}), 400

    key = get_groq_key()
    if not key:
        return jsonify({"error": "Groq API key not configured. Ask your admin."}), 400

    try:
        from groq import Groq
        client = Groq(api_key=key)

        prompt = (
            f"Write a {tone} email newsletter about '{topic}' for '{audience}'.\n"
            f"Include {num_sections} content sections plus a CTA button labelled '{cta_text}'.\n"
            "Return ONLY valid JSON (no text outside it):\n"
            "{\n"
            '  "subject": "Compelling subject line, max 60 chars",\n'
            '  "content_html": "<div> ... full self-contained HTML newsletter fragment with inline styles. '
            "Start with <div style='font-family:Arial,sans-serif;max-width:600px;margin:0 auto;'>. "
            "Include: a header banner with gradient background, bold newsletter title, intro paragraph, "
            f"{num_sections} clearly titled sections with body text, a prominent CTA button, and a footer. "
            "All styles must be inline. Colours should match a "
            f"{tone} tone. Make it visually rich and professional."
            ' ... </div>"\n'
            "}"
        )

        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You are an expert email designer. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=3000,
        )

        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())

        nl = Newsletter(
            subject=parsed["subject"],
            topic=topic,
            audience=audience,
            tone=tone,
            content_html=parsed["content_html"],
        )
        db.session.add(nl)
        db.session.commit()

        return jsonify({
            "success": True,
            "id": nl.id,
            "subject": nl.subject,
            "content_html": nl.content_html,
        })

    except json.JSONDecodeError:
        return jsonify({"error": "AI returned an invalid response. Please try again."}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@emailnewsgen_bp.route("/<int:nl_id>/download")
def download(nl_id):
    nl = Newsletter.query.get_or_404(nl_id)
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{nl.subject}</title>
</head>
<body style="margin:0;padding:20px;background:#f4f4f4;font-family:Arial,sans-serif;">
{nl.content_html}
</body>
</html>"""
    safe = "".join(c for c in nl.subject[:30] if c.isalnum() or c in " _-").strip().replace(" ", "_")
    filename = f"Newsletter_{nl.id}_{safe}.html"
    return Response(
        full_html,
        mimetype="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@emailnewsgen_bp.route("/<int:nl_id>/delete", methods=["POST"])
def delete(nl_id):
    nl = Newsletter.query.get_or_404(nl_id)
    db.session.delete(nl)
    db.session.commit()
    return jsonify({"success": True})
