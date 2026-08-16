# People Directory

The People directory is the setup surface for relationship-aware communication.

It combines three kinds of evidence without weakening VAAPP's identity boundary:

1. Android phone-book contacts.
2. Google Contacts.
3. Existing relationship identities learned from Gmail, SMS, calls, messaging notifications and Calendar.

## Identity and merge rule

Names, groups, organizations and relationship labels are presentation/context metadata. They never merge two people by themselves.

Automatic convergence requires an exact normalized email address or phone number already accepted by Relationship Memory. This preserves the existing rule that two people named `Alex` remain separate unless they share a verified identity.

Android and Google source records are stored separately in `contact_source_records` and link to a canonical `RelationshipProfile` only after identity resolution.

## Imported metadata

The Android reader imports only contact-directory fields needed for relationship setup:

- display name
- phone numbers
- email addresses
- organization
- job title
- department
- nickname
- contact groups
- relationship labels
- starred/favorite state

It deliberately does not import contact photos, notes, postal addresses, birthdays or unrelated contact data.

Google directory sync uses People API contact fields for the same purpose, including contact-group memberships and `relations`.

## Relationship-category suggestions

Source metadata may suggest an existing personalized-reply category such as `partner`, `family`, `friend`, `client`, `provider` or `colleague`.

Suggestions are advisory only. They do not:

- save communication preferences automatically;
- authorize sending;
- grant financial/material authority;
- merge identities;
- disclose sensitive information.

The user makes the category authoritative by saving that person's Personalized Replies settings.

## Android flow

`People → Sync contacts` reads the phone book through the existing `READ_CONTACTS` permission, uploads a bounded snapshot in chunks, then refreshes Google Contacts when Google is connected.

A complete snapshot deactivates phone-book source rows that disappeared from the device without deleting the canonical Relationship Memory or communication evidence.

## UI

The People screen is name-first and alphabetic rather than message-first. It provides:

- search by name, phone, email, company, group or relationship metadata;
- All / Personalized / Not configured / Favorites filters;
- source badges for Phone and Google;
- group/category context;
- direct navigation to the existing per-person Personalized Replies editor.

A name-only contact with no stable phone/email is visible but cannot be bound to personalized replies until a stable identity exists.
