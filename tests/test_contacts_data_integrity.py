"""Contact edits and portable exports must preserve the user's data."""

import copy
import csv
import io

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.contacts_routes as contacts


@pytest.fixture
def local_store(monkeypatch, tmp_path):
    monkeypatch.setattr(contacts, "DATA_DIR", tmp_path)
    monkeypatch.setattr(contacts, "LOCAL_CONTACTS_FILE", tmp_path / "contacts.json")
    monkeypatch.setattr(contacts, "_get_carddav_config", lambda: {"url": "", "username": "", "password": ""})
    monkeypatch.setattr(contacts, "_contact_cache", {
        "contacts": [], "by_owner": {}, "fetched_at": None, "failed_at": None,
    })


def test_vcf_export_preserves_postal_address():
    original = {"uid": "address", "name": "Casey", "emails": ["casey@example.test"],
                "phones": ["111"], "address": "42 Fixture Street, Sydney"}
    assert contacts._parse_vcards(contacts._contacts_to_vcf([original])) == [original]


def test_csv_export_retains_original_columns_for_existing_readers():
    exported = contacts._contacts_to_csv([{
        "uid": "legacy", "name": "Casey", "emails": ["casey@example.test", "work@example.test"],
        "phones": ["111", "222"], "address": "42 Fixture Street",
    }])
    reader = csv.DictReader(io.StringIO(exported))
    assert reader.fieldnames[:3] == ["name", "email", "phone"]
    row = next(reader)
    assert (row["name"], row["email"], row["phone"]) == ("Casey", "casey@example.test", "111")


@pytest.mark.parametrize("original", [
    [{"uid": "one", "name": "Casey", "emails": ["home@example.test"], "phones": ["111", "222"], "address": "42 Fixture Street"}],
    [{"uid": "two", "name": "Casey", "emails": ["home@example.test", "work@example.test"], "phones": ["111"], "address": "Unit 4; Level 2"}],
    [{"uid": "phone", "name": "Phone only", "emails": [], "phones": ["111", "222"], "address": ""}],
    [{"uid": "unnamed", "name": "", "emails": [], "phones": ["111"], "address": ""}],
    [{"uid": "address", "name": "Address only", "emails": [], "phones": [], "address": 'Suite "A", Sydney\nAustralia'}],
    [{"uid": "a", "name": "Same Name", "emails": ["a@example.test"], "phones": [], "address": ""},
     {"uid": "b", "name": "Same Name", "emails": ["b@example.test"], "phones": [], "address": ""}],
])
def test_csv_roundtrip_keeps_contact_identity_and_all_fields(local_store, original):
    exported = contacts._contacts_to_csv(original)
    result = contacts._import_csv_contacts(exported)
    assert result == {"imported": len(original), "failed": 0, "total": len(original)}
    assert contacts._load_local_contacts() == original
    assert contacts._import_csv_contacts(exported)["imported"] == 0
    assert contacts._load_local_contacts() == original


@pytest.mark.parametrize("text", [
    "name,email,phone\nCasey,casey@example.test,111\n",
    "full_name,email_address,tel\nCasey,casey@example.test,111\n",
    "Casey,casey@example.test,111\n",
])
def test_legacy_csv_still_imports(local_store, text):
    assert contacts._import_csv_contacts(text)["imported"] == 1
    row = contacts._load_local_contacts()[0]
    assert (row["name"], row["emails"], row["phones"]) == ("Casey", ["casey@example.test"], ["111"])


@pytest.mark.parametrize("name,email,phone", [
    ("Phone", "phone@example.test", "+61 (2) 1234-5678"),
    ("Mail", "mail@example.test", "+61 (2) 1234-5678"),
    ("Name", "", "+61 (2) 1234-5678"),
    ("Phone", "", "555-0100 ext 42"),
    ("Mail", "", "555-0100 x42"),
])
def test_headerless_csv_name_matching_header_keeps_all_rows(local_store, name, email, phone):
    text = f"{name},{email},{phone}\nCasey,casey@example.test,222\n"
    assert contacts._import_csv_contacts(text) == {"imported": 2, "failed": 0, "total": 2}
    rows = contacts._load_local_contacts()
    assert [(row["name"], row["emails"], row["phones"]) for row in rows] == [
        (name, [email] if email else [], [phone]),
        ("Casey", ["casey@example.test"], ["222"]),
    ]


def test_csv_rejects_malformed_list_instead_of_losing_data(local_store):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["uid", "name", "emails", "phones", "address"])
    writer.writerow(["broken", "Casey", '["unfinished"', '["111"]', ""])
    result = contacts._import_csv_contacts(out.getvalue())
    assert result["imported"] == 0
    assert result["failed"] == 1
    assert contacts._load_local_contacts() == []


def test_explicit_empty_address_is_saved_through_route(local_store, monkeypatch):
    original = {"uid": "clear", "name": "Casey", "emails": ["casey@example.test"],
                "phones": ["111"], "address": "42 Fixture Street"}
    contacts._save_local_contacts([original])
    app = FastAPI()
    app.dependency_overrides[contacts.require_admin] = lambda: "admin"
    monkeypatch.setattr(contacts, "effective_user", lambda request: None)
    app.include_router(contacts.setup_contacts_routes())
    with TestClient(app) as client:
        response = client.put("/api/contacts/clear", json={
            "name": "Casey", "emails": original["emails"], "phones": original["phones"], "address": "",
        })
    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert contacts._load_local_contacts()[0]["address"] == ""


def test_omitted_address_is_preserved(local_store):
    contacts._save_local_contacts([{"uid": "keep", "name": "Casey", "address": "42 Fixture Street"}])
    assert contacts._update_contact("keep", "Renamed", [], [])
    assert contacts._load_local_contacts()[0]["address"] == "42 Fixture Street"


def test_lowercase_vcards_keep_separate_contacts(local_store):
    text = "".join(
        f"begin:vcard\r\nversion:4.0\r\nuid:{uid}\r\nfn:{uid}\r\nemail:{uid}@example.test\r\nend:vcard\r\n"
        for uid in ("one", "two")
    )
    parsed = contacts._parse_vcards(text)
    assert [(c["uid"], c["name"], c["emails"]) for c in parsed] == [
        ("one", "one", ["one@example.test"]), ("two", "two", ["two@example.test"]),
    ]
    assert contacts._import_vcards(text) == {"imported": 2, "failed": 0, "total": 2}
    assert contacts._load_local_contacts() == parsed


@pytest.mark.parametrize("address", ["Unit 4; Level 2", r"A\; B", r"A\\; B", "Comma, semicolon; newline\nsecond line"])
def test_vcard_address_escaping_roundtrip(address):
    card = contacts._build_vcard("Casey", "casey@example.test", address=address)
    assert contacts._parse_vcards(card)[0]["address"] == address


def test_property_matching_does_not_consume_quoted_parameter_colons():
    card = 'BEGIN:VCARD\nVERSION:4.0\nFN:Casey\nADR;LABEL="https://example.test":;;Unit 4\\; Level 2;;;;\nEND:VCARD\n'
    assert contacts._parse_vcards(card)[0]["address"] == "Unit 4; Level 2"


@pytest.fixture
def carddav(monkeypatch):
    raw = (
        "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:remote\r\nFN:Casey\r\nN:;Casey;;;\r\n"
        "item1.EMAIL;TYPE=HOME:casey@example.test\r\nitem1.X-ABLabel:Home\r\n"
        "ADR;TYPE=HOME:;;42 Fixture Street;;;;\r\nORG:Fixture Company\r\n"
        "bday:19900102\r\nNOTE:Keep this note\r\nPHOTO;ENCODING=b:YWJj\r\n ZGVm\r\nEND:VCARD\r\n"
    )
    state = {"raw": raw, "puts": [], "get_status": 200, "put_status": 204}
    monkeypatch.setattr(contacts, "_get_carddav_config", lambda: {"url": "https://dav.example.test/book", "username": "", "password": ""})
    monkeypatch.setattr(contacts, "_resolve_resource_url", lambda uid: "https://dav.example.test/book/remote.vcf")
    monkeypatch.setattr(contacts, "_contact_cache", {
        "contacts": [], "by_owner": {}, "fetched_at": None, "failed_at": None,
    })

    def get(url, **kwargs):
        return httpx.Response(state["get_status"], text=state["raw"], headers={"ETag": '"v1"'})

    def put(url, **kwargs):
        state["puts"].append((url, copy.deepcopy(kwargs)))
        if state["put_status"] == 204:
            state["raw"] = kwargs["data"].decode()
        return httpx.Response(state["put_status"])

    monkeypatch.setattr(contacts.httpx, "get", get)
    monkeypatch.setattr(contacts.httpx, "put", put)
    return state


def test_carddav_rename_preserves_other_fields_and_parameters(carddav):
    assert contacts._update_contact("remote", "Renamed", ["casey@example.test"], [], "42 Fixture Street")
    raw = carddav["raw"]
    for value in ("ORG:Fixture Company", "bday:19900102", "NOTE:Keep this note", "PHOTO;ENCODING=b:YWJjZGVm",
                  "item1.EMAIL;TYPE=HOME:casey@example.test", "item1.X-ABLabel:Home", "ADR;TYPE=HOME:;;42 Fixture Street;;;;"):
        assert value in raw
    assert contacts._parse_vcards(raw)[0]["name"] == "Renamed"
    assert carddav["puts"][0][1]["headers"]["If-Match"] == '"v1"'


def test_carddav_address_can_be_cleared_without_removing_metadata(carddav):
    assert contacts._update_contact("remote", "Casey", ["casey@example.test"], [], "")
    assert contacts._parse_vcards(carddav["raw"])[0]["address"] == ""
    assert "ORG:Fixture Company" in carddav["raw"]


@pytest.mark.parametrize("failure", ["unreadable", "conflict", "wrong_uid"])
def test_carddav_update_refuses_unsafe_replacement(carddav, failure):
    before = carddav["raw"]
    if failure == "unreadable":
        carddav["get_status"] = 503
    elif failure == "conflict":
        carddav["put_status"] = 412
    else:
        carddav["raw"] = before.replace("UID:remote", "UID:someone-else")
    assert not contacts._update_contact("remote", "Renamed", ["casey@example.test"], [], "")
    if failure != "conflict":
        assert carddav["puts"] == []
    else:
        assert carddav["raw"] == before


def test_carddav_import_preserves_lowercase_card_identity(carddav):
    text = "begin:vcard\nversion:4.0\nuid:one\nfn:One\nend:vcard\n" \
           "begin:vcard\nversion:4.0\nuid:two\nfn:Two\nend:vcard\n"
    # Keep URL validation independent of DNS/network availability.
    from unittest.mock import patch
    with patch.object(contacts, "_carddav_base_url", lambda cfg: cfg["url"]):
        assert contacts._import_vcards(text) == {"imported": 2, "failed": 0, "total": 2}
    assert [url.rsplit("/", 1)[1] for url, _ in carddav["puts"]] == ["one.vcf", "two.vcf"]
    assert all("UID:" not in kwargs["data"].decode() for _, kwargs in carddav["puts"])
