package com.fulltimeva.full_time_va

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.provider.ContactsContract

object VaContacts {
    private data class ContactData(
        val id: Long,
        val externalId: String,
        val displayName: String,
        val starred: Boolean,
        val phones: LinkedHashSet<String> = linkedSetOf(),
        val emails: LinkedHashSet<String> = linkedSetOf(),
        var organization: String = "",
        var jobTitle: String = "",
        var department: String = "",
        var nickname: String = "",
        val groups: LinkedHashSet<String> = linkedSetOf(),
        val relations: MutableList<Map<String, String>> = mutableListOf(),
    )

    fun read(context: Context): Map<String, Any> {
        if (
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.M &&
            context.checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED
        ) {
            return mapOf(
                "granted" to false,
                "count" to 0,
                "contacts" to emptyList<Map<String, Any>>(),
            )
        }

        val contacts = linkedMapOf<Long, ContactData>()
        context.contentResolver.query(
            ContactsContract.Contacts.CONTENT_URI,
            arrayOf(
                ContactsContract.Contacts._ID,
                ContactsContract.Contacts.LOOKUP_KEY,
                ContactsContract.Contacts.DISPLAY_NAME_PRIMARY,
                ContactsContract.Contacts.STARRED,
            ),
            null,
            null,
            "${ContactsContract.Contacts.DISPLAY_NAME_PRIMARY} COLLATE LOCALIZED ASC",
        )?.use { cursor ->
            val idIndex = cursor.getColumnIndexOrThrow(ContactsContract.Contacts._ID)
            val lookupIndex = cursor.getColumnIndexOrThrow(ContactsContract.Contacts.LOOKUP_KEY)
            val nameIndex = cursor.getColumnIndexOrThrow(ContactsContract.Contacts.DISPLAY_NAME_PRIMARY)
            val starredIndex = cursor.getColumnIndexOrThrow(ContactsContract.Contacts.STARRED)
            while (cursor.moveToNext()) {
                val id = cursor.getLong(idIndex)
                val lookup = cursor.getString(lookupIndex).orEmpty().ifBlank { "contact:$id" }
                contacts[id] = ContactData(
                    id = id,
                    externalId = lookup.take(320),
                    displayName = cursor.getString(nameIndex).orEmpty().trim().take(255),
                    starred = cursor.getInt(starredIndex) != 0,
                )
            }
        }

        if (contacts.isEmpty()) {
            return mapOf("granted" to true, "count" to 0, "contacts" to emptyList<Map<String, Any>>())
        }

        readPhones(context, contacts)
        readEmails(context, contacts)
        readStructuredData(context, contacts)

        val result = contacts.values.map { contact ->
            mapOf<String, Any>(
                "external_id" to contact.externalId,
                "display_name" to contact.displayName,
                "phones" to contact.phones.toList(),
                "emails" to contact.emails.toList(),
                "organization" to contact.organization,
                "job_title" to contact.jobTitle,
                "department" to contact.department,
                "nickname" to contact.nickname,
                "groups" to contact.groups.toList(),
                "relations" to contact.relations,
                "starred" to contact.starred,
            )
        }
        return mapOf("granted" to true, "count" to result.size, "contacts" to result)
    }

    private fun readPhones(context: Context, contacts: MutableMap<Long, ContactData>) {
        context.contentResolver.query(
            ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
            arrayOf(
                ContactsContract.CommonDataKinds.Phone.CONTACT_ID,
                ContactsContract.CommonDataKinds.Phone.NUMBER,
                ContactsContract.CommonDataKinds.Phone.NORMALIZED_NUMBER,
            ),
            null,
            null,
            null,
        )?.use { cursor ->
            val contactIndex = cursor.getColumnIndexOrThrow(ContactsContract.CommonDataKinds.Phone.CONTACT_ID)
            val numberIndex = cursor.getColumnIndexOrThrow(ContactsContract.CommonDataKinds.Phone.NUMBER)
            val normalizedIndex = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NORMALIZED_NUMBER)
            while (cursor.moveToNext()) {
                val contact = contacts[cursor.getLong(contactIndex)] ?: continue
                val normalized = if (normalizedIndex >= 0) cursor.getString(normalizedIndex).orEmpty().trim() else ""
                val raw = cursor.getString(numberIndex).orEmpty().trim()
                if (normalized.isNotBlank()) contact.phones.add(normalized.take(120))
                if (raw.isNotBlank() && raw != normalized) contact.phones.add(raw.take(120))
            }
        }
    }

    private fun readEmails(context: Context, contacts: MutableMap<Long, ContactData>) {
        context.contentResolver.query(
            ContactsContract.CommonDataKinds.Email.CONTENT_URI,
            arrayOf(
                ContactsContract.CommonDataKinds.Email.CONTACT_ID,
                ContactsContract.CommonDataKinds.Email.ADDRESS,
            ),
            null,
            null,
            null,
        )?.use { cursor ->
            val contactIndex = cursor.getColumnIndexOrThrow(ContactsContract.CommonDataKinds.Email.CONTACT_ID)
            val addressIndex = cursor.getColumnIndexOrThrow(ContactsContract.CommonDataKinds.Email.ADDRESS)
            while (cursor.moveToNext()) {
                val contact = contacts[cursor.getLong(contactIndex)] ?: continue
                val email = cursor.getString(addressIndex).orEmpty().trim()
                if (email.isNotBlank()) contact.emails.add(email.take(500))
            }
        }
    }

    private fun readStructuredData(context: Context, contacts: MutableMap<Long, ContactData>) {
        val groupNames = readGroupNames(context)
        val mimeTypes = arrayOf(
            ContactsContract.CommonDataKinds.Organization.CONTENT_ITEM_TYPE,
            ContactsContract.CommonDataKinds.Relation.CONTENT_ITEM_TYPE,
            ContactsContract.CommonDataKinds.GroupMembership.CONTENT_ITEM_TYPE,
            ContactsContract.CommonDataKinds.Nickname.CONTENT_ITEM_TYPE,
        )
        val placeholders = mimeTypes.joinToString(",") { "?" }
        context.contentResolver.query(
            ContactsContract.Data.CONTENT_URI,
            arrayOf(
                ContactsContract.Data.CONTACT_ID,
                ContactsContract.Data.MIMETYPE,
                ContactsContract.Data.DATA1,
                ContactsContract.Data.DATA2,
                ContactsContract.Data.DATA3,
                ContactsContract.Data.DATA4,
                ContactsContract.Data.DATA5,
            ),
            "${ContactsContract.Data.MIMETYPE} IN ($placeholders)",
            mimeTypes,
            null,
        )?.use { cursor ->
            val contactIndex = cursor.getColumnIndexOrThrow(ContactsContract.Data.CONTACT_ID)
            val mimeIndex = cursor.getColumnIndexOrThrow(ContactsContract.Data.MIMETYPE)
            val data1 = cursor.getColumnIndexOrThrow(ContactsContract.Data.DATA1)
            val data2 = cursor.getColumnIndexOrThrow(ContactsContract.Data.DATA2)
            val data3 = cursor.getColumnIndexOrThrow(ContactsContract.Data.DATA3)
            val data4 = cursor.getColumnIndexOrThrow(ContactsContract.Data.DATA4)
            val data5 = cursor.getColumnIndexOrThrow(ContactsContract.Data.DATA5)
            while (cursor.moveToNext()) {
                val contact = contacts[cursor.getLong(contactIndex)] ?: continue
                when (cursor.getString(mimeIndex)) {
                    ContactsContract.CommonDataKinds.Organization.CONTENT_ITEM_TYPE -> {
                        if (contact.organization.isBlank()) {
                            contact.organization = cursor.getString(data1).orEmpty().trim().take(255)
                        }
                        if (contact.jobTitle.isBlank()) {
                            contact.jobTitle = cursor.getString(data4).orEmpty().trim().take(255)
                        }
                        if (contact.department.isBlank()) {
                            contact.department = cursor.getString(data5).orEmpty().trim().take(255)
                        }
                    }
                    ContactsContract.CommonDataKinds.Nickname.CONTENT_ITEM_TYPE -> {
                        if (contact.nickname.isBlank()) {
                            contact.nickname = cursor.getString(data1).orEmpty().trim().take(255)
                        }
                    }
                    ContactsContract.CommonDataKinds.GroupMembership.CONTENT_ITEM_TYPE -> {
                        val groupId = cursor.getString(data1).orEmpty()
                        val name = groupNames[groupId].orEmpty().trim()
                        if (name.isNotBlank()) contact.groups.add(name.take(160))
                    }
                    ContactsContract.CommonDataKinds.Relation.CONTENT_ITEM_TYPE -> {
                        val person = cursor.getString(data1).orEmpty().trim().take(255)
                        val type = cursor.getString(data2)?.toIntOrNull()
                            ?: ContactsContract.CommonDataKinds.Relation.TYPE_CUSTOM
                        val label = cursor.getString(data3)
                        val typeLabel = relationType(type, label)
                        if (person.isNotBlank() || typeLabel.isNotBlank()) {
                            val relation = mapOf("type" to typeLabel, "person" to person)
                            if (!contact.relations.contains(relation)) contact.relations.add(relation)
                        }
                    }
                }
            }
        }
    }


    private fun relationType(type: Int, customLabel: String?): String = when (type) {
        ContactsContract.CommonDataKinds.Relation.TYPE_ASSISTANT -> "assistant"
        ContactsContract.CommonDataKinds.Relation.TYPE_BROTHER -> "brother"
        ContactsContract.CommonDataKinds.Relation.TYPE_CHILD -> "child"
        ContactsContract.CommonDataKinds.Relation.TYPE_DOMESTIC_PARTNER -> "partner"
        ContactsContract.CommonDataKinds.Relation.TYPE_FATHER -> "father"
        ContactsContract.CommonDataKinds.Relation.TYPE_FRIEND -> "friend"
        ContactsContract.CommonDataKinds.Relation.TYPE_MANAGER -> "manager"
        ContactsContract.CommonDataKinds.Relation.TYPE_MOTHER -> "mother"
        ContactsContract.CommonDataKinds.Relation.TYPE_PARENT -> "parent"
        ContactsContract.CommonDataKinds.Relation.TYPE_PARTNER -> "partner"
        ContactsContract.CommonDataKinds.Relation.TYPE_REFERRED_BY -> "referred_by"
        ContactsContract.CommonDataKinds.Relation.TYPE_RELATIVE -> "relative"
        ContactsContract.CommonDataKinds.Relation.TYPE_SISTER -> "sister"
        ContactsContract.CommonDataKinds.Relation.TYPE_SPOUSE -> "spouse"
        else -> customLabel.orEmpty().trim().ifBlank { "custom" }.take(80)
    }

    private fun readGroupNames(context: Context): Map<String, String> {
        val groups = linkedMapOf<String, String>()
        context.contentResolver.query(
            ContactsContract.Groups.CONTENT_URI,
            arrayOf(
                ContactsContract.Groups._ID,
                ContactsContract.Groups.TITLE,
                ContactsContract.Groups.DELETED,
            ),
            null,
            null,
            null,
        )?.use { cursor ->
            val idIndex = cursor.getColumnIndexOrThrow(ContactsContract.Groups._ID)
            val titleIndex = cursor.getColumnIndexOrThrow(ContactsContract.Groups.TITLE)
            val deletedIndex = cursor.getColumnIndex(ContactsContract.Groups.DELETED)
            while (cursor.moveToNext()) {
                if (deletedIndex >= 0 && cursor.getInt(deletedIndex) != 0) continue
                val title = cursor.getString(titleIndex).orEmpty().trim()
                if (title.isNotBlank()) groups[cursor.getLong(idIndex).toString()] = title.take(160)
            }
        }
        return groups
    }
}
