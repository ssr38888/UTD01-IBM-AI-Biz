"""Contract 4: claim row -> plain-language "what happens next" letter, plus a post-check.

The template owns every fact. Granite only rewrites tone. check_letter() rejects any
number, form code or risk/dispute wording that the template did not put there.
"""
import json
import re
from datetime import date
from pathlib import Path

from granite import generate

CODES = json.loads((Path(__file__).resolve().parent / "wcio_codes.json").read_text())
# Public NY WCB contacts; re-check on wcb.ny.gov before any real mailing.
AIW_PHONE = "(800) 580-6665"
AIW_EMAIL = "advinjured@wcb.ny.gov"

TEMPLATES = {
    "en": """Dear injured worker,

The New York Workers' Compensation Board has opened your claim (reference {claim_id}) on {opened}. It is about a work injury: {injury}, {body_part}. This letter explains what happens next. You do not need to reply to it.

What to send:
- If you have not filed Form C-3 (Employee Claim), file it online at wcb.ny.gov. You have up to 2 years from the accident.
- Tell your employer about the injury in writing, if you have not already. The rule is within 30 days.
- Keep copies of every medical bill, receipt and doctor's note.

Your rights:
- Medical care for a work injury is paid by your employer's insurance, not by you. Use a doctor authorized by the Board.
- You can get wage benefits if the injury keeps you out of work for more than 7 days.
- Your employer cannot punish or fire you for filing a claim.
- You do not need a lawyer to file. Help from the Board is free.

Who to call:
The Advocate for Injured Workers at {phone} or {email}. They work for you, at no cost.

This letter was drafted with AI help and reviewed by a Board advocate.""",
    "es": """Estimado trabajador lesionado:

La Junta de Compensación Laboral de Nueva York abrió su reclamo (referencia {claim_id}) el {opened}. Se trata de una lesión en el trabajo: {injury}, {body_part}. Esta carta explica los próximos pasos. No necesita responderla.

Qué enviar:
- Si no ha presentado el Formulario C-3 (Reclamo del Empleado), preséntelo en línea en wcb.ny.gov. Tiene hasta 2 años desde el accidente.
- Informe a su empleador sobre la lesión por escrito, si aún no lo ha hecho. La regla es dentro de 30 días.
- Guarde copias de cada factura médica, recibo y nota del médico.

Sus derechos:
- La atención médica por una lesión de trabajo la paga el seguro de su empleador, no usted. Use un médico autorizado por la Junta.
- Puede recibir beneficios de salario si la lesión le impide trabajar por más de 7 días.
- Su empleador no puede castigarlo ni despedirlo por presentar un reclamo.
- No necesita un abogado para presentar su reclamo. La ayuda de la Junta es gratuita.

A quién llamar:
Al Defensor de Trabajadores Lesionados al {phone} o {email}. Trabajan para usted, sin costo.

Esta carta fue redactada con ayuda de IA y revisada por un defensor de la Junta.""",
}
MONTHS = {
    "en": "January February March April May June July August September October November December".split(),
    "es": "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split(),
}
INSTRUCTION = {
    "en": "Rewrite the letter below so it sounds warm and is easy to read (6th-grade level). "
          "Keep every number, date, phone number, email, web address and form name exactly as written. "
          "Do not add facts, promises, or advice. Do not mention risk, scores, predictions or disputes. "
          "Output only the letter.",
    "es": "Reescriba la carta de abajo para que suene cálida y sea fácil de leer (nivel de sexto grado). "
          "Mantenga cada número, fecha, teléfono, correo, dirección web y nombre de formulario exactamente igual. "
          "No agregue datos, promesas ni consejos. No mencione riesgo, puntajes, predicciones ni disputas. "
          "Escriba solo la carta.",
}
BANNED = re.compile(r"disput|controvert|deni|deny|risk|score|predict|algorithm|riesgo|puntaje|predic|negad|deneg", re.I)
TOKENS = re.compile(r"\b[A-Z]-\d+(?:\.\d+)?\b|\d+")  # form codes like C-3, then any number


def _words(table, code):
    return CODES[table].get(str(code), "details on file").lower() if str(code) not in ("", "nan", "MISSING") else "details on file"


def template(row, lang="en"):
    opened = date.fromisoformat(str(row["assembly_date"])[:10])
    month = MONTHS[lang][opened.month - 1]
    return TEMPLATES[lang].format(
        claim_id=row["claim_id"], phone=AIW_PHONE, email=AIW_EMAIL,
        opened=f"{month} {opened.day}, {opened.year}" if lang == "en" else f"{opened.day} de {month} de {opened.year}",
        injury=_words("nature", row["nature_code"]), body_part=_words("body_part", row["body_part_code"]),
    )


def stream_letter(row, lang="en"):
    return generate(f"{INSTRUCTION[lang]}\n\nLETTER: {template(row, lang)}")


def check_letter(text, row, lang="en"):
    """Problems found; empty list = safe to show the advocate. Falls back to the template on failure."""
    source = template(row, lang)
    allowed = set(TOKENS.findall(source))
    problems = [f"Not in template: {t}" for t in sorted(set(TOKENS.findall(text)) - allowed)]
    problems += [f"Banned wording: {m}" for m in sorted({m.group(0).lower() for m in BANNED.finditer(text)})]
    problems += [f"Missing: {must}" for must in (AIW_PHONE, str(row["claim_id"])) if must not in text]
    return problems


if __name__ == "__main__":
    row = {"claim_id": 6708415, "assembly_date": "2025-03-07", "nature_code": "52", "body_part_code": "42"}
    for lang in TEMPLATES:
        letter = "".join(stream_letter(row, lang))
        assert check_letter(letter, row, lang) == [], check_letter(letter, row, lang)
    assert check_letter(letter + " Your claim may be disputed, call 555-0100.", row, "es") == [
        "Not in template: 0100", "Not in template: 555", "Banned wording: disput"]
    print("letter self-check ok")
