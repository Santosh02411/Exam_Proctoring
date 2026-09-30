import io
import zipfile
from xml.etree import ElementTree as ET

from tests.conftest import (
    register_and_verify, login, add_single_question, add_multi_question, add_short_question,
)

QTI_NS = "{http://www.imsglobal.org/xsd/imsqti_v2p1}"


def _setup_test(client, app, admin_email, test_code):
    register_and_verify(client, app, "Admin", admin_email, "9144455001", "admin", "Adminpass1!")
    login(client, admin_email, "Adminpass1!")
    client.post("/admin/tests/create", data=dict(
        test_code=test_code, title="QTI Export Test", description="d", duration_minutes=20,
        total_questions=4, passing_marks=1, status="published", max_attempts=1,
        negative_marks_per_wrong=0))
    with app.app_context():
        from app.models import Test
        test_id = Test.query.filter_by(test_code=test_code).first().id
    return test_id


def test_qti_export_produces_valid_zip_with_manifest(client, app):
    test_id = _setup_test(client, app, "qtia1@test.com", "QTI1")
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    login(client, "qtia1@test.com", "Adminpass1!")

    r = client.get(f"/admin/tests/{test_id}/export-qti")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/zip"
    assert "QTI1_qti.zip" in r.headers["Content-Disposition"]

    zf = zipfile.ZipFile(io.BytesIO(r.data))
    names = zf.namelist()
    assert "imsmanifest.xml" in names
    assert "items/Q1.xml" in names

    manifest = ET.fromstring(zf.read("imsmanifest.xml"))
    assert manifest.tag.endswith("manifest")


def test_qti_single_choice_encodes_correct_answer(client, app):
    test_id = _setup_test(client, app, "qtia2@test.com", "QTI2")
    add_single_question(client, test_id, "Capital of France?", "Berlin", "Paris", "Rome", "Madrid", "b", marks=2)
    login(client, "qtia2@test.com", "Adminpass1!")

    r = client.get(f"/admin/tests/{test_id}/export-qti")
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    item = ET.fromstring(zf.read("items/Q1.xml"))

    response_decl = item.find(f"{QTI_NS}responseDeclaration")
    assert response_decl.get("cardinality") == "single"
    correct_values = [v.text for v in response_decl.find(f"{QTI_NS}correctResponse").findall(f"{QTI_NS}value")]
    assert correct_values == ["ChoiceB"]

    choices = item.find(f"{QTI_NS}itemBody").find(f"{QTI_NS}choiceInteraction").findall(f"{QTI_NS}simpleChoice")
    choice_texts = {c.get("identifier"): c.text for c in choices}
    assert choice_texts["ChoiceB"] == "Paris"
    assert "Capital of France?" in ET.tostring(item, encoding="unicode")


def test_qti_multi_choice_encodes_all_correct_answers(client, app):
    test_id = _setup_test(client, app, "qtia3@test.com", "QTI3")
    add_multi_question(client, test_id, "Primes?", "2", "4", "3", "6", ["a", "c"], marks=2)
    login(client, "qtia3@test.com", "Adminpass1!")

    r = client.get(f"/admin/tests/{test_id}/export-qti")
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    item = ET.fromstring(zf.read("items/Q1.xml"))

    response_decl = item.find(f"{QTI_NS}responseDeclaration")
    assert response_decl.get("cardinality") == "multiple"
    correct_values = sorted(v.text for v in response_decl.find(f"{QTI_NS}correctResponse").findall(f"{QTI_NS}value"))
    assert correct_values == ["ChoiceA", "ChoiceC"]


def test_qti_short_answer_uses_text_entry_interaction(client, app):
    test_id = _setup_test(client, app, "qtia4@test.com", "QTI4")
    add_short_question(client, test_id, "1+1=?", "2", marks=1)
    login(client, "qtia4@test.com", "Adminpass1!")

    r = client.get(f"/admin/tests/{test_id}/export-qti")
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    item = ET.fromstring(zf.read("items/Q1.xml"))

    response_decl = item.find(f"{QTI_NS}responseDeclaration")
    assert response_decl.get("baseType") == "string"
    correct_value = response_decl.find(f"{QTI_NS}correctResponse").find(f"{QTI_NS}value").text
    assert correct_value == "2"
    assert item.find(f"{QTI_NS}itemBody").find(f".//{QTI_NS}textEntryInteraction") is not None


def test_qti_export_skips_descriptive_questions(client, app):
    test_id = _setup_test(client, app, "qtia5@test.com", "QTI5")
    add_single_question(client, test_id, "2+2=?", "3", "4", "5", "6", "b", marks=1)
    with app.app_context():
        from app.models import Question, Test
        from app import db
        test = Test.query.get(test_id)
        db.session.add(Question(
            test_id=test_id, question_type="descriptive", question_text="Explain gravity.",
            marks=5, correct_answer="",
        ))
        db.session.commit()

    login(client, "qtia5@test.com", "Adminpass1!")
    r = client.get(f"/admin/tests/{test_id}/export-qti")
    zf = zipfile.ZipFile(io.BytesIO(r.data))
    # Only the one auto-graded question should have made it into the package.
    item_files = [n for n in zf.namelist() if n.startswith("items/")]
    assert len(item_files) == 1


def test_qti_export_requires_admin_of_same_org(client, app):
    test_id = _setup_test(client, app, "qtia6@test.com", "QTI6")
    client.get("/logout")
    register_and_verify(client, app, "OtherAdmin", "qtia6b@test.com", "9144455002", "admin", "Adminpass1!")
    with app.app_context():
        from app.models import User, Organization
        from app import db
        other_org = Organization(name="A Different Org", slug="a-different-org", status="active")
        db.session.add(other_org)
        db.session.flush()
        other_admin = User.query.filter_by(email="qtia6b@test.com").first()
        other_admin.org_id = other_org.id
        db.session.commit()

    login(client, "qtia6b@test.com", "Adminpass1!")
    r = client.get(f"/admin/tests/{test_id}/export-qti")
    assert r.status_code in (403, 404)
