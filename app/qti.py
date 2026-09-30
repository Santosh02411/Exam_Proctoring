"""QTI Export — packages a test's auto-graded questions as an IMS QTI 2.1
item package: one assessmentItem XML per question plus an imsmanifest.xml,
zipped together. QTI 2.1 is the version with the broadest real-world
import support (Canvas, Moodle, Blackboard, Respondus, D2L all read it),
so it's the target here rather than the newer QTI 3.0.

Deliberately scoped to a QUESTION/ITEM package, not a full assessmentTest
(the QTI construct that bundles items into a timed, sectioned test with
its own navigation rules) — that's a substantially larger spec surface
(test parts, navigation modes, time limits, weight processing) for
something every mainstream LMS already lets you build from an imported
item bank anyway. Importing this package gives you this test's questions
as items ready to drop into a new quiz on the other end, which is what
"export my questions to Canvas/Moodle" concretely means for the vast
majority of use cases.

Only auto-graded question types export (single/multi/true_false/short/
fill_blank) — descriptive/coding questions have no standard QTI
interaction that captures "a human manually grades this," so including
them would produce an item that silently imports as something it isn't.
They're skipped, and the caller is told how many were skipped so this
isn't a silent partial export.
"""
import io
import zipfile
from xml.etree import ElementTree as ET
from xml.dom import minidom

QTI_NS = "http://www.imsglobal.org/xsd/imsqti_v2p1"
QTI_SCHEMA_LOCATION = (
    "http://www.imsglobal.org/xsd/imsqti_v2p1 "
    "http://www.imsglobal.org/xsd/qti/qtiv2p1/imsqti_v2p1.xsd"
)
MATCH_CORRECT_TEMPLATE = "http://www.imsglobal.org/question/qti_v2p1/rptemplates/match_correct"

# Which question types this exporter can represent at all.
EXPORTABLE_TYPES = {"single", "multi", "true_false", "short", "fill_blank"}

CHOICE_IDS = ["ChoiceA", "ChoiceB", "ChoiceC", "ChoiceD"]
LETTER_TO_CHOICE_ID = dict(zip("abcd", CHOICE_IDS))


def _pretty(elem):
    rough = ET.tostring(elem, encoding="utf-8")
    return minidom.parseString(rough).toprettyxml(indent="  ", encoding="UTF-8")


def _choice_item_xml(question, item_id):
    """single / multi / true_false -> a choiceInteraction assessmentItem."""
    root = ET.Element("assessmentItem", {
        "xmlns": QTI_NS,
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": QTI_SCHEMA_LOCATION,
        "identifier": item_id,
        "title": item_id,
        "adaptive": "false",
        "timeDependent": "false",
    })

    if question.question_type == "true_false":
        options = [("a", "True"), ("b", "False")]
        correct_letters = ["a"] if question.correct_answer.strip().lower() == "true" else ["b"]
        cardinality = "single"
    else:
        options = [
            (letter, getattr(question, f"option_{letter}"))
            for letter in "abcd" if getattr(question, f"option_{letter}")
        ]
        if question.question_type == "multi":
            correct_letters = [l.strip() for l in question.correct_answer.split(",") if l.strip()]
            cardinality = "multiple"
        else:  # single
            correct_letters = [question.correct_answer.strip()]
            cardinality = "single"

    response_decl = ET.SubElement(root, "responseDeclaration", {
        "identifier": "RESPONSE", "cardinality": cardinality, "baseType": "identifier",
    })
    correct_resp = ET.SubElement(response_decl, "correctResponse")
    for letter in correct_letters:
        choice_id = LETTER_TO_CHOICE_ID.get(letter)
        if choice_id:
            v = ET.SubElement(correct_resp, "value")
            v.text = choice_id

    outcome_decl = ET.SubElement(root, "outcomeDeclaration", {
        "identifier": "SCORE", "cardinality": "single", "baseType": "float",
    })
    default_val = ET.SubElement(outcome_decl, "defaultValue")
    ET.SubElement(default_val, "value").text = "0"

    item_body = ET.SubElement(root, "itemBody")
    p = ET.SubElement(item_body, "p")
    p.text = question.question_text

    max_choices = 1 if cardinality == "single" else len(options)
    choice_interaction = ET.SubElement(item_body, "choiceInteraction", {
        "responseIdentifier": "RESPONSE", "shuffle": "false", "maxChoices": str(max_choices),
    })
    for letter, text in options:
        choice = ET.SubElement(choice_interaction, "simpleChoice", {
            "identifier": LETTER_TO_CHOICE_ID[letter],
        })
        choice.text = text

    ET.SubElement(root, "responseProcessing", {"template": MATCH_CORRECT_TEMPLATE})
    return root


def _text_entry_item_xml(question, item_id):
    """short / fill_blank -> a textEntryInteraction assessmentItem. Graded
    as an exact string match by the standard match_correct template —
    this app's own case-insensitive/multi-accepted-answer grading (see
    Question.score_for) doesn't have a direct QTI 2.1 equivalent without
    hand-writing custom responseProcessing, so only the FIRST accepted
    answer (for fill_blank's ';'-separated list) is exported as the
    correct response. Noted in the export summary shown to the admin
    rather than silently losing the other accepted answers."""
    root = ET.Element("assessmentItem", {
        "xmlns": QTI_NS,
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": QTI_SCHEMA_LOCATION,
        "identifier": item_id,
        "title": item_id,
        "adaptive": "false",
        "timeDependent": "false",
    })

    first_answer = question.correct_answer.split(";")[0].strip()

    response_decl = ET.SubElement(root, "responseDeclaration", {
        "identifier": "RESPONSE", "cardinality": "single", "baseType": "string",
    })
    correct_resp = ET.SubElement(response_decl, "correctResponse")
    ET.SubElement(correct_resp, "value").text = first_answer

    outcome_decl = ET.SubElement(root, "outcomeDeclaration", {
        "identifier": "SCORE", "cardinality": "single", "baseType": "float",
    })
    default_val = ET.SubElement(outcome_decl, "defaultValue")
    ET.SubElement(default_val, "value").text = "0"

    item_body = ET.SubElement(root, "itemBody")
    p = ET.SubElement(item_body, "p")
    p.text = question.question_text
    ET.SubElement(p, "textEntryInteraction", {"responseIdentifier": "RESPONSE", "expectedLength": "20"})

    ET.SubElement(root, "responseProcessing", {"template": MATCH_CORRECT_TEMPLATE})
    return root


def question_to_qti_item(question, item_id):
    if question.question_type in ("single", "multi", "true_false"):
        return _choice_item_xml(question, item_id)
    if question.question_type in ("short", "fill_blank"):
        return _text_entry_item_xml(question, item_id)
    return None


def _manifest_xml(test, item_ids):
    root = ET.Element("manifest", {
        "identifier": f"MANIFEST-{test.test_code}",
        "xmlns": "http://www.imsglobal.org/xsd/imscp_v1p1",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": (
            "http://www.imsglobal.org/xsd/imscp_v1p1 http://www.imsglobal.org/xsd/imscp_v1p1.xsd "
            "http://www.imsglobal.org/xsd/imsqti_v2p1 http://www.imsglobal.org/xsd/qti/qtiv2p1/imsqti_v2p1p1.xsd"
        ),
    })
    ET.SubElement(root, "organizations")
    resources = ET.SubElement(root, "resources")
    for item_id in item_ids:
        resource = ET.SubElement(resources, "resource", {
            "identifier": item_id, "type": "imsqti_item_xmlv2p1", "href": f"items/{item_id}.xml",
        })
        ET.SubElement(resource, "file", {"href": f"items/{item_id}.xml"})
    return root


def generate_qti_package(test):
    """Returns (zip_bytes, exported_count, skipped_count). Skipped
    questions are ones whose type isn't in EXPORTABLE_TYPES (descriptive/
    coding) — see the module docstring for why those can't round-trip
    through QTI at all."""
    questions = sorted(test.questions, key=lambda q: q.id)
    exportable = [q for q in questions if q.question_type in EXPORTABLE_TYPES]
    skipped = len(questions) - len(exportable)

    buf = io.BytesIO()
    item_ids = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, q in enumerate(exportable, start=1):
            item_id = f"Q{i}"
            item_xml = question_to_qti_item(q, item_id)
            if item_xml is None:
                continue
            item_ids.append(item_id)
            zf.writestr(f"items/{item_id}.xml", _pretty(item_xml))
        zf.writestr("imsmanifest.xml", _pretty(_manifest_xml(test, item_ids)))

    return buf.getvalue(), len(item_ids), skipped


def package_filename(test):
    safe_code = "".join(c for c in test.test_code if c.isalnum() or c in ("-", "_")) or "exam"
    return f"{safe_code}_qti.zip"
