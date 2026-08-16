from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_people_directory_keeps_identity_matching_strict_and_relationship_metadata_advisory():
    service = _read("backend/app/services/contact_directory.py")
    model = _read("backend/app/models/people_entities.py")
    assert "ContactSourceRecord" in model
    assert "relationship_id" in model
    assert "_profile_for_identities" in service
    assert 'source_type == "android_contacts"' in service
    assert "suggested_category" in service
    assert "relationship_category" in service
    assert "name-only phone/Google contacts" in service


def test_android_phone_book_reader_uses_existing_contacts_permission_and_no_contact_photos():
    manifest = _read("android/android/app/src/main/AndroidManifest.xml")
    activity = _read(
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/MainActivity.kt"
    )
    contacts = _read(
        "android/android/app/src/main/kotlin/com/fulltimeva/full_time_va/VaContacts.kt"
    )
    assert "android.permission.READ_CONTACTS" in manifest
    assert '"readPhoneContacts"' in activity
    assert "GroupMembership.CONTENT_ITEM_TYPE" in contacts
    assert "Relation.CONTENT_ITEM_TYPE" in contacts
    assert "Organization.CONTENT_ITEM_TYPE" in contacts
    assert "Nickname.CONTENT_ITEM_TYPE" in contacts
    assert "PHOTO" not in contacts
    assert "NOTE" not in contacts


def test_people_screen_is_dedicated_and_opens_existing_personalized_reply_editor():
    home = _read("android/lib/screens/home_shell.dart")
    people = _read("android/lib/screens/people_directory_page.dart")
    assert "PeopleDirectoryPage" in home
    assert "People & personalized replies" in home
    assert "Personalized" in people
    assert "Not configured" in people
    assert "Favorites" in people
    assert "RelationshipPreferencesPage" in people
    assert "readPhoneContacts" in people
    assert "/api/relationships/directory/sync-google" in people


def test_people_static_routes_are_registered_before_legacy_dynamic_relationship_route():
    main = _read("backend/app/main.py")
    legacy_routes = _read("backend/app/api/routes.py")
    people_routes = _read("backend/app/api/v105_routes.py")
    assert '@router.get("/api/relationships/{relationship_id}")' in legacy_routes
    assert '@router.get("/api/relationships/directory")' in people_routes
    assert main.index("app.include_router(v105_router)") < main.index("app.include_router(router)")
