"""The fictional candidate used by the end-to-end suite (never a real person)."""

PROFILE = {
    "name": "Alex Example",
    "email": "alex@example.com",
    "phone": "5550100",
    "phone_country_code": "+1",
    "linkedin": "https://www.linkedin.com/in/alex-example",
    "github": "https://github.com/alex-example",
    "website": "https://alex.example.com",
    "address": {"city": "Springfield", "state": "Illinois", "zip": "62701", "country": "United States of America"},
    "current_role": "Data Engineer",
    "current_company": "Example Labs",
    "years_of_experience": "6",
    "work_authorization": True,
    "visa_sponsorship_needed": False,
    "willing_to_relocate": True,
    "notice_period": "Two weeks",
    "salary_expectation": {"min": 150000, "max": 180000, "currency": "USD"},
    "education": {"school": "Example State University", "degree": "B.S. Computer Science"},
    "cover_letter": ("Dear hiring team, I have spent six years building reliable data pipelines in Python "
                     "at Example Labs and would like to bring that experience to your team."),
    "eeo": {},
}

QA = {"Are you open to relocation?": "Yes"}

RESUME_FACTS = "Six years building data pipelines in Python at Example Labs; B.S. Computer Science."

RESUME_TEXT = (
    "Alex Example\nalex@example.com | +1 555-0100 | Springfield, IL\n\n"
    "Experience\nData Engineer, Example Labs (2020-present)\n- Built Python data pipelines\n\n"
    "Education\nB.S. Computer Science, Example State University\n"
)


def essay_for(label: str) -> str:
    """A truthful essay built only from the resume facts (stand-in for the agent's writing)."""
    return f"{RESUME_FACTS} That experience is why this role fits: {label.strip().rstrip('?*')[:60]}."


def write_resume_pdf(path) -> str:
    """A tiny but valid one-page PDF resume."""
    from pathlib import Path
    lines = RESUME_TEXT.splitlines()
    text_ops = "BT /F1 11 Tf 50 750 Td 14 TL " + " ".join(
        "(" + l.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '" for l in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(text_ops)} >>\nstream\n{text_ops}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out.encode("latin-1")))
        out += f"{i} 0 obj\n{o}\nendobj\n"
    xref = len(out.encode("latin-1"))
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(out.encode("latin-1"))
    return str(p)
