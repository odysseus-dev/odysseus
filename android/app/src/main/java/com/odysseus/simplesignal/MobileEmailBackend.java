package com.odysseus.simplesignal;

import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLConnection;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.SecureRandom;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;
import java.util.TimeZone;
import java.util.UUID;

import javax.activation.DataHandler;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.mail.Address;
import javax.mail.BodyPart;
import javax.mail.FetchProfile;
import javax.mail.Flags;
import javax.mail.Folder;
import javax.mail.Message;
import javax.mail.MessagingException;
import javax.mail.Multipart;
import javax.mail.Part;
import javax.mail.Session;
import javax.mail.Store;
import javax.mail.Transport;
import javax.mail.UIDFolder;
import javax.mail.internet.InternetAddress;
import javax.mail.internet.MimeBodyPart;
import javax.mail.internet.MimeMessage;
import javax.mail.internet.MimeMultipart;
import javax.mail.internet.MimeUtility;
import javax.mail.search.FlagTerm;
import javax.mail.util.ByteArrayDataSource;

class MobileEmailBackend {
    static class Response {
        final int status;
        final Object body;

        Response(int status, Object body) {
            this.status = status;
            this.body = body;
        }
    }

    private static final String PREF_EMAIL_ACCOUNTS = "email_accounts";
    private static final String PREF_ENDPOINTS = "endpoints";
    private static final String PREF_DEFAULT_ENDPOINT = "default_endpoint_id";
    private static final String KEY_ALIAS = "odysseus_mobile_email_credentials";
    private static final String ENC_PREFIX = "enc:v1:";
    private static final int GCM_TAG_BITS = 128;
    private static final int MAIL_TIMEOUT_MS = 15000;
    private static final int LLM_TIMEOUT_MS = 120000;
    private static final int MAX_COMPOSE_UPLOAD_BYTES = 25 * 1024 * 1024;

    private final SharedPreferences prefs;
    private final File filesDir;

    MobileEmailBackend(SharedPreferences prefs, File filesDir) {
        this.prefs = prefs;
        this.filesDir = filesDir;
    }

    Response route(
            String method,
            String tail,
            Map<String, String> query,
            Map<String, String> body,
            Map<String, String> headers,
            byte[] rawBody
    ) throws Exception {
        if ("GET".equals(method) && "/accounts".equals(tail)) {
            return ok(new JSONObject().put("accounts", publicAccounts()));
        }
        if ("POST".equals(method) && "/accounts/test".equals(tail)) {
            return ok(testAccount(body));
        }
        if ("POST".equals(method) && "/accounts".equals(tail)) {
            return ok(createAccount(body));
        }
        if (tail.startsWith("/accounts/")) {
            return routeAccount(method, tail.substring("/accounts/".length()), body);
        }
        if ("GET".equals(method) && "/config".equals(tail)) {
            JSONObject account = defaultAccount();
            return ok(account == null ? new JSONObject() : publicAccount(account));
        }
        if ("PUT".equals(method) && "/config".equals(tail)) {
            JSONObject existing = defaultAccount();
            if (existing == null) return ok(createAccount(body));
            return ok(updateAccount(existing.optString("id"), body));
        }
        if ("GET".equals(method) && "/style".equals(tail)) {
            return ok(new JSONObject().put("ok", true).put("style", ""));
        }
        if ("POST".equals(method) && "/style".equals(tail)) {
            return ok(new JSONObject().put("ok", true));
        }
        if ("POST".equals(method) && "/extract-style".equals(tail)) {
            return ok(new JSONObject().put("ok", false).put("error", "Email style extraction runs on the desktop backend."));
        }
        if ("GET".equals(method) && "/urgency-state".equals(tail)) {
            return ok(new JSONObject().put("total_unread", 0).put("total_urgent", 0).put("max_score", 0).put("per_uid", new JSONObject()));
        }
        if ("GET".equals(method) && "/folders".equals(tail)) {
            return ok(listFolders(query));
        }
        if ("GET".equals(method) && "/list".equals(tail)) {
            return ok(listEmails(query));
        }
        if ("GET".equals(method) && "/search".equals(tail)) {
            return ok(searchEmails(query));
        }
        if ("GET".equals(method) && tail.startsWith("/read/")) {
            return ok(readEmail(tail.substring("/read/".length()), query));
        }
        if ("POST".equals(method) && "/send".equals(tail)) {
            return sendEmail(body);
        }
        if (tail.startsWith("/mark-read/") && "POST".equals(method)) {
            return ok(setFlag(tail.substring("/mark-read/".length()), query, Flags.Flag.SEEN, true));
        }
        if (tail.startsWith("/mark-unread/") && "POST".equals(method)) {
            return ok(setFlag(tail.substring("/mark-unread/".length()), query, Flags.Flag.SEEN, false));
        }
        if (tail.startsWith("/mark-answered/") && "POST".equals(method)) {
            return ok(setFlag(tail.substring("/mark-answered/".length()), query, Flags.Flag.ANSWERED, true));
        }
        if (tail.startsWith("/clear-answered/") && "POST".equals(method)) {
            return ok(setFlag(tail.substring("/clear-answered/".length()), query, Flags.Flag.ANSWERED, false));
        }
        if (tail.startsWith("/archive/") && "POST".equals(method)) {
            return ok(moveMessage(tail.substring("/archive/".length()), query, detectArchiveFolderName(query)));
        }
        if (tail.startsWith("/move/") && "POST".equals(method)) {
            return ok(moveMessage(tail.substring("/move/".length()), query, valueOr(query.get("dest"), "")));
        }
        if (tail.startsWith("/delete-permanent/") && "DELETE".equals(method)) {
            return ok(deleteMessage(tail.substring("/delete-permanent/".length()), query));
        }
        if (tail.startsWith("/delete/") && "DELETE".equals(method)) {
            return ok(deleteMessage(tail.substring("/delete/".length()), query));
        }
        if ("GET".equals(method) && "/scheduled".equals(tail)) {
            return ok(new JSONObject().put("items", new JSONArray()));
        }
        if ("POST".equals(method) && "/summarize".equals(tail)) {
            return summarizeEmail(body);
        }
        if ("POST".equals(method) && "/ai-reply".equals(tail)) {
            return aiReply(body);
        }
        if ("POST".equals(method) && "/compose-upload".equals(tail)) {
            return ok(composeUpload(headers, rawBody));
        }
        if ("DELETE".equals(method) && tail.startsWith("/compose-upload/")) {
            return ok(deleteComposeUpload(tail.substring("/compose-upload/".length())));
        }
        return new Response(404, new JSONObject().put("detail", "Mobile email route not implemented"));
    }

    private Response routeAccount(String method, String tail, Map<String, String> body) throws Exception {
        String[] parts = tail.split("/");
        String id = parts.length > 0 ? parts[0] : "";
        if (id.isEmpty()) return new Response(404, new JSONObject().put("error", "Account not found"));
        if ("POST".equals(method) && parts.length > 1 && "set-default".equals(parts[1])) {
            return ok(setDefaultAccount(id));
        }
        if ("PUT".equals(method)) return ok(updateAccount(id, body));
        if ("DELETE".equals(method)) return ok(deleteAccount(id));
        return new Response(404, new JSONObject().put("error", "Account not found"));
    }

    private Response ok(Object body) {
        return new Response(200, body);
    }

    private JSONObject createAccount(Map<String, String> data) throws Exception {
        JSONArray accounts = loadAccounts();
        JSONObject account = new JSONObject()
                .put("id", UUID.randomUUID().toString().replace("-", ""))
                .put("created_at", System.currentTimeMillis())
                .put("enabled", true);
        applyAccountFields(account, data, true);
        if (!account.has("name") || account.optString("name").trim().isEmpty()) {
            account.put("name", firstNonEmpty(account.optString("from_address"), account.optString("imap_user"), "Email"));
        }

        boolean makeDefault = accounts.length() == 0 || parseBool(data.get("is_default"), false);
        if (makeDefault) clearDefault(accounts);
        account.put("is_default", makeDefault);
        accounts.put(account);
        saveAccounts(accounts);
        return new JSONObject().put("ok", true).put("id", account.optString("id"));
    }

    private JSONObject updateAccount(String id, Map<String, String> data) throws Exception {
        JSONArray accounts = loadAccounts();
        for (int i = 0; i < accounts.length(); i++) {
            JSONObject account = accounts.getJSONObject(i);
            if (!id.equals(account.optString("id"))) continue;
            applyAccountFields(account, data, false);
            if (parseBool(data.get("is_default"), account.optBoolean("is_default", false))) {
                clearDefault(accounts);
                account.put("is_default", true);
            }
            accounts.put(i, account);
            saveAccounts(accounts);
            return new JSONObject().put("ok", true).put("id", id);
        }
        return new JSONObject().put("ok", false).put("error", "Account not found");
    }

    private JSONObject deleteAccount(String id) throws Exception {
        JSONArray accounts = loadAccounts();
        JSONArray kept = new JSONArray();
        boolean removedDefault = false;
        for (int i = 0; i < accounts.length(); i++) {
            JSONObject account = accounts.getJSONObject(i);
            if (id.equals(account.optString("id"))) {
                removedDefault = account.optBoolean("is_default", false);
                continue;
            }
            kept.put(account);
        }
        if (removedDefault && kept.length() > 0) {
            kept.getJSONObject(0).put("is_default", true);
        }
        saveAccounts(kept);
        return new JSONObject().put("ok", true);
    }

    private JSONObject setDefaultAccount(String id) throws Exception {
        JSONArray accounts = loadAccounts();
        boolean found = false;
        for (int i = 0; i < accounts.length(); i++) {
            JSONObject account = accounts.getJSONObject(i);
            boolean match = id.equals(account.optString("id"));
            account.put("is_default", match);
            if (match) found = true;
            accounts.put(i, account);
        }
        if (!found) return new JSONObject().put("ok", false).put("error", "Account not found");
        saveAccounts(accounts);
        return new JSONObject().put("ok", true);
    }

    private JSONObject testAccount(Map<String, String> data) throws Exception {
        JSONObject hydrated = hydrateForTest(data);
        JSONObject imap = new JSONObject().put("ok", false);
        JSONObject smtp = null;

        if (firstNonEmpty(hydrated.optString("imap_host"), hydrated.optString("imap_user"), hydrated.optString("imap_password")).isEmpty()
                || hydrated.optString("imap_host").isEmpty()
                || hydrated.optString("imap_user").isEmpty()
                || hydrated.optString("imap_password").isEmpty()) {
            imap.put("error", "Need IMAP host, username, and password");
        } else {
            try {
                Store store = openImapStore(hydrated);
                try {
                    store.close();
                } catch (Exception ignored) {}
                imap = new JSONObject().put("ok", true);
            } catch (Exception ex) {
                imap = new JSONObject().put("ok", false).put("error", friendlyMailError("IMAP", ex));
            }
        }

        if (!hydrated.optString("smtp_host").trim().isEmpty()) {
            try {
                Transport transport = openSmtpTransport(hydrated);
                try {
                    transport.close();
                } catch (Exception ignored) {}
                smtp = new JSONObject().put("ok", true);
            } catch (Exception ex) {
                smtp = new JSONObject().put("ok", false).put("error", friendlyMailError("SMTP", ex));
            }
        }

        return new JSONObject()
                .put("ok", imap.optBoolean("ok") && (smtp == null || smtp.optBoolean("ok")))
                .put("imap", imap)
                .put("smtp", smtp == null ? JSONObject.NULL : smtp);
    }

    private JSONObject listFolders(Map<String, String> query) throws Exception {
        JSONObject account = resolveAccount(query.get("account_id"));
        Store store = openImapStore(account);
        try {
            JSONArray folders = new JSONArray();
            List<String> names = new ArrayList<>();
            collectFolders(store.getDefaultFolder(), names);
            if (!names.contains("INBOX")) names.add(0, "INBOX");
            Collections.sort(names, String.CASE_INSENSITIVE_ORDER);
            if (names.remove("INBOX")) folders.put("INBOX");
            for (String name : names) folders.put(name);
            return new JSONObject().put("folders", folders);
        } finally {
            safeClose(store);
        }
    }

    private JSONObject listEmails(Map<String, String> query) throws Exception {
        JSONObject account = resolveAccount(query.get("account_id"));
        String folderName = valueOr(query.get("folder"), "INBOX");
        int limit = clamp(parseInt(query.get("limit"), 50), 1, 200);
        int offset = Math.max(0, parseInt(query.get("offset"), 0));
        String filter = valueOr(query.get("filter"), "all").toLowerCase(Locale.US);
        String from = valueOr(query.get("from"), "").toLowerCase(Locale.US).trim();
        boolean hasAttachmentsOnly = "1".equals(valueOr(query.get("has_attachments"), ""));

        Store store = openImapStore(account);
        Folder folder = null;
        try {
            folder = openMailFolder(store, folderName, Folder.READ_ONLY);
            UIDFolder uidFolder = (UIDFolder) folder;
            Message[] candidates;
            int totalMessages = folder.getMessageCount();
            if ("unread".equals(filter)) {
                candidates = folder.search(new FlagTerm(new Flags(Flags.Flag.SEEN), false));
            } else if ("unanswered".equals(filter) || "undone".equals(filter)) {
                candidates = folder.search(new FlagTerm(new Flags(Flags.Flag.ANSWERED), false));
            } else {
                int end = Math.max(1, totalMessages - offset);
                int start = Math.max(1, end - limit + 1);
                candidates = totalMessages <= 0 ? new Message[0] : folder.getMessages(start, end);
            }
            List<Message> messages = new ArrayList<>();
            Collections.addAll(messages, candidates);
            messages.sort((a, b) -> Integer.compare(b.getMessageNumber(), a.getMessageNumber()));
            if (!"all".equals(filter) && messages.size() > offset) {
                messages = new ArrayList<>(messages.subList(offset, Math.min(messages.size(), offset + limit)));
            } else if (!"all".equals(filter)) {
                messages = new ArrayList<>();
            }
            FetchProfile fp = new FetchProfile();
            fp.add(FetchProfile.Item.ENVELOPE);
            fp.add(FetchProfile.Item.FLAGS);
            fp.add(FetchProfile.Item.CONTENT_INFO);
            fp.add(UIDFolder.FetchProfileItem.UID);
            if (!messages.isEmpty()) folder.fetch(messages.toArray(new Message[0]), fp);

            JSONArray emails = new JSONArray();
            for (Message message : messages) {
                JSONObject item = messageSummary(message, uidFolder);
                if (!from.isEmpty() && !item.optString("from_address").toLowerCase(Locale.US).contains(from)) continue;
                if (hasAttachmentsOnly && !item.optBoolean("has_attachments")) continue;
                emails.put(item);
                if (emails.length() >= limit) break;
            }
            int total = "all".equals(filter) ? totalMessages : candidates.length;
            return new JSONObject()
                    .put("emails", emails)
                    .put("total", total)
                    .put("folder", folderName)
                    .put("offset", offset);
        } finally {
            safeClose(folder, false);
            safeClose(store);
        }
    }

    private JSONObject searchEmails(Map<String, String> query) throws Exception {
        Map<String, String> widened = new HashMap<>(query);
        widened.put("filter", "all");
        widened.put("limit", valueOr(query.get("limit"), "100"));
        JSONObject result = listEmails(widened);
        String q = valueOr(query.get("q"), "").toLowerCase(Locale.US).trim();
        if (q.isEmpty()) return result;
        JSONArray filtered = new JSONArray();
        JSONArray emails = result.optJSONArray("emails");
        for (int i = 0; emails != null && i < emails.length(); i++) {
            JSONObject email = emails.getJSONObject(i);
            String haystack = (email.optString("subject") + " " + email.optString("from_name") + " "
                    + email.optString("from_address") + " " + email.optString("to") + " " + email.optString("cc"))
                    .toLowerCase(Locale.US);
            if (haystack.contains(q)) filtered.put(email);
        }
        return new JSONObject()
                .put("emails", filtered)
                .put("total", filtered.length())
                .put("folder", valueOr(query.get("folder"), "INBOX"))
                .put("offset", 0);
    }

    private JSONObject readEmail(String uidRaw, Map<String, String> query) throws Exception {
        JSONObject account = resolveAccount(query.get("account_id"));
        String folderName = valueOr(query.get("folder"), "INBOX");
        boolean markSeen = !"false".equalsIgnoreCase(valueOr(query.get("mark_seen"), "true"));
        Store store = openImapStore(account);
        Folder folder = null;
        try {
            folder = openMailFolder(store, folderName, markSeen ? Folder.READ_WRITE : Folder.READ_ONLY);
            UIDFolder uidFolder = (UIDFolder) folder;
            Message message = uidFolder.getMessageByUID(parseLong(uidRaw, -1));
            if (message == null) return new JSONObject().put("error", "Email UID " + uidRaw + " not found");
            if (markSeen) message.setFlag(Flags.Flag.SEEN, true);
            return messageDetail(message, uidFolder, folderName);
        } finally {
            safeClose(folder, false);
            safeClose(store);
        }
    }

    private JSONObject setFlag(String uidRaw, Map<String, String> query, Flags.Flag flag, boolean value) throws Exception {
        JSONObject account = resolveAccount(query.get("account_id"));
        Store store = openImapStore(account);
        Folder folder = null;
        try {
            folder = openMailFolder(store, valueOr(query.get("folder"), "INBOX"), Folder.READ_WRITE);
            Message message = ((UIDFolder) folder).getMessageByUID(parseLong(uidRaw, -1));
            if (message == null) return new JSONObject().put("success", false).put("error", "Email not found");
            message.setFlag(flag, value);
            return new JSONObject().put("success", true).put("ok", true);
        } finally {
            safeClose(folder, false);
            safeClose(store);
        }
    }

    private JSONObject deleteMessage(String uidRaw, Map<String, String> query) throws Exception {
        JSONObject account = resolveAccount(query.get("account_id"));
        Store store = openImapStore(account);
        Folder folder = null;
        try {
            folder = openMailFolder(store, valueOr(query.get("folder"), "INBOX"), Folder.READ_WRITE);
            Message message = ((UIDFolder) folder).getMessageByUID(parseLong(uidRaw, -1));
            if (message == null) return new JSONObject().put("success", false).put("error", "Email not found");
            message.setFlag(Flags.Flag.DELETED, true);
            return new JSONObject().put("success", true).put("deleted", true);
        } finally {
            safeClose(folder, true);
            safeClose(store);
        }
    }

    private JSONObject moveMessage(String uidRaw, Map<String, String> query, String destName) throws Exception {
        if (destName == null || destName.trim().isEmpty()) {
            return new JSONObject().put("success", false).put("error", "No destination folder found");
        }
        JSONObject account = resolveAccount(query.get("account_id"));
        Store store = openImapStore(account);
        Folder source = null;
        try {
            source = openMailFolder(store, valueOr(query.get("folder"), "INBOX"), Folder.READ_WRITE);
            Message message = ((UIDFolder) source).getMessageByUID(parseLong(uidRaw, -1));
            if (message == null) return new JSONObject().put("success", false).put("error", "Email not found");
            Folder dest = store.getFolder(destName);
            if (!dest.exists()) dest.create(Folder.HOLDS_MESSAGES);
            source.copyMessages(new Message[]{message}, dest);
            message.setFlag(Flags.Flag.DELETED, true);
            return new JSONObject().put("success", true).put("ok", true);
        } finally {
            safeClose(source, true);
            safeClose(store);
        }
    }

    private JSONObject composeUpload(Map<String, String> headers, byte[] rawBody) throws Exception {
        String contentType = valueOr(headers.get("content-type"), "");
        String boundary = multipartBoundary(contentType);
        if (boundary.isEmpty()) return new JSONObject().put("success", false).put("error", "Missing multipart boundary");
        UploadPart part = extractFirstFilePart(rawBody, boundary);
        if (part == null || part.data.length == 0) {
            return new JSONObject().put("success", false).put("error", "No attachment file found");
        }
        if (part.data.length > MAX_COMPOSE_UPLOAD_BYTES) {
            return new JSONObject().put("success", false).put("error", "Attachment is too large");
        }
        String safeName = safeFilename(firstNonEmpty(part.filename, "attachment"));
        String token = UUID.randomUUID().toString().replace("-", "") + "_" + safeName;
        File file = new File(uploadDir(), token);
        try (FileOutputStream out = new FileOutputStream(file)) {
            out.write(part.data);
        }
        return new JSONObject()
                .put("success", true)
                .put("token", token)
                .put("filename", safeName)
                .put("size", part.data.length);
    }

    private JSONObject deleteComposeUpload(String token) throws Exception {
        File file = composeUploadFile(token);
        if (file.exists()) file.delete();
        return new JSONObject().put("success", true);
    }

    private Response sendEmail(Map<String, String> body) throws Exception {
        JSONObject account;
        try {
            account = resolveSendAccount(body.get("account_id"));
        } catch (Exception ex) {
            return new Response(400, new JSONObject().put("success", false).put("error", ex.getMessage()));
        }
        if (account == null) {
            return new Response(400, new JSONObject()
                    .put("success", false)
                    .put("error", "No email account configured. Add one in Settings > Integrations > Email."));
        }
        if (account.optString("smtp_host").trim().isEmpty()) {
            return new Response(400, new JSONObject().put("success", false).put("error", "No SMTP server configured for this account"));
        }
        if (firstNonEmpty(account.optString("smtp_user"), account.optString("imap_user")).trim().isEmpty()) {
            return new Response(400, new JSONObject().put("success", false).put("error", "No SMTP username configured for this account"));
        }
        if (firstNonEmpty(credential(account, "smtp_password"), credential(account, "imap_password")).trim().isEmpty()) {
            return new Response(400, new JSONObject().put("success", false).put("error", "No SMTP password configured for this account"));
        }
        JSONArray attachments = parseAttachments(valueOr(body.get("attachments"), ""));
        try {
            Session session = Session.getInstance(smtpProperties(account));
            MimeMessage message = new MimeMessage(session);
            String from = firstNonEmpty(account.optString("from_address"), account.optString("smtp_user"), account.optString("imap_user"));
            message.setFrom(new InternetAddress(from));
            addRecipients(message, Message.RecipientType.TO, body.get("to"));
            addRecipients(message, Message.RecipientType.CC, body.get("cc"));
            addRecipients(message, Message.RecipientType.BCC, body.get("bcc"));
            message.setSubject(valueOr(body.get("subject"), ""), "UTF-8");
            message.setSentDate(new Date());
            message.setHeader("Message-ID", "<" + UUID.randomUUID() + "@odysseus.mobile>");
            if (!valueOr(body.get("in_reply_to"), "").trim().isEmpty()) {
                message.setHeader("In-Reply-To", body.get("in_reply_to"));
            }
            if (!valueOr(body.get("references"), "").trim().isEmpty()) {
                message.setHeader("References", body.get("references"));
            }

            String text = valueOr(body.get("body"), "");
            String html = valueOr(body.get("body_html"), "");
            MimeMultipart alternative = new MimeMultipart("alternative");
            MimeBodyPart plainPart = new MimeBodyPart();
            plainPart.setText(text, "UTF-8");
            alternative.addBodyPart(plainPart);
            if (!html.trim().isEmpty()) {
                MimeBodyPart htmlPart = new MimeBodyPart();
                htmlPart.setContent(html, "text/html; charset=UTF-8");
                alternative.addBodyPart(htmlPart);
            }
            if (attachments.length() > 0) {
                MimeMultipart mixed = new MimeMultipart("mixed");
                MimeBodyPart bodyPart = new MimeBodyPart();
                bodyPart.setContent(alternative);
                mixed.addBodyPart(bodyPart);
                attachComposeUploads(mixed, attachments);
                message.setContent(mixed);
            } else if (!html.trim().isEmpty()) {
                message.setContent(alternative);
            } else {
                message.setText(text, "UTF-8");
            }
            message.saveChanges();

            Transport transport = openSmtpTransport(account);
            try {
                transport.sendMessage(message, message.getAllRecipients());
            } finally {
                try {
                    transport.close();
                } catch (Exception ignored) {}
            }

            String sentFolder = appendSent(account, message);
            return ok(new JSONObject()
                    .put("success", true)
                    .put("account_id", account.optString("id"))
                    .put("sent_folder", sentFolder == null ? JSONObject.NULL : sentFolder)
                    .put("sent_uid", JSONObject.NULL)
                    .put("message_id", message.getMessageID()));
        } catch (Exception ex) {
            return new Response(502, new JSONObject().put("success", false).put("error", friendlyMailError("SMTP", ex)));
        }
    }

    private JSONArray parseAttachments(String raw) {
        String value = valueOr(raw, "").trim();
        if (value.isEmpty() || "null".equalsIgnoreCase(value)) return new JSONArray();
        try {
            return new JSONArray(value);
        } catch (Exception ignored) {
            return new JSONArray();
        }
    }

    private void attachComposeUploads(MimeMultipart mixed, JSONArray attachments) throws Exception {
        for (int i = 0; i < attachments.length(); i++) {
            JSONObject item = attachments.optJSONObject(i);
            if (item == null) continue;
            String token = item.optString("token");
            File file = composeUploadFile(token);
            if (!file.exists() || !file.isFile()) {
                throw new IllegalStateException("Missing attachment: " + item.optString("filename", token));
            }
            byte[] bytes = readFile(file);
            String filename = safeFilename(firstNonEmpty(item.optString("filename"), file.getName()));
            String mime = firstNonEmpty(URLConnection.guessContentTypeFromName(filename), "application/octet-stream");
            MimeBodyPart attach = new MimeBodyPart();
            attach.setDataHandler(new DataHandler(new ByteArrayDataSource(bytes, mime)));
            attach.setFileName(MimeUtility.encodeText(filename, "UTF-8", null));
            mixed.addBodyPart(attach);
        }
    }

    private UploadPart extractFirstFilePart(byte[] rawBody, String boundary) throws Exception {
        String raw = new String(rawBody, StandardCharsets.ISO_8859_1);
        String marker = "--" + boundary;
        int pos = raw.indexOf(marker);
        while (pos >= 0) {
            int partStart = pos + marker.length();
            if (raw.startsWith("--", partStart)) return null;
            if (raw.startsWith("\r\n", partStart)) partStart += 2;
            int headerEnd = raw.indexOf("\r\n\r\n", partStart);
            if (headerEnd < 0) return null;
            String headerBlock = raw.substring(partStart, headerEnd);
            int dataStart = headerEnd + 4;
            int next = raw.indexOf("\r\n" + marker, dataStart);
            if (next < 0) next = raw.indexOf(marker, dataStart);
            if (next < 0) return null;
            String disposition = headerLine(headerBlock, "content-disposition");
            String filename = contentDispositionParam(disposition, "filename");
            if (!filename.isEmpty()) {
                return new UploadPart(filename, Arrays.copyOfRange(rawBody, dataStart, next));
            }
            pos = raw.indexOf(marker, next + marker.length());
        }
        return null;
    }

    private String multipartBoundary(String contentType) {
        for (String token : valueOr(contentType, "").split(";")) {
            String part = token.trim();
            if (part.toLowerCase(Locale.US).startsWith("boundary=")) {
                String boundary = part.substring("boundary=".length()).trim();
                if (boundary.startsWith("\"") && boundary.endsWith("\"") && boundary.length() >= 2) {
                    boundary = boundary.substring(1, boundary.length() - 1);
                }
                return boundary;
            }
        }
        return "";
    }

    private String headerLine(String headerBlock, String name) {
        String wanted = name.toLowerCase(Locale.US) + ":";
        for (String line : headerBlock.split("\\r?\\n")) {
            String trimmed = line.trim();
            if (trimmed.toLowerCase(Locale.US).startsWith(wanted)) {
                return trimmed.substring(wanted.length()).trim();
            }
        }
        return "";
    }

    private String contentDispositionParam(String disposition, String key) {
        String wanted = key.toLowerCase(Locale.US) + "=";
        for (String token : valueOr(disposition, "").split(";")) {
            String part = token.trim();
            if (!part.toLowerCase(Locale.US).startsWith(wanted)) continue;
            String value = part.substring(wanted.length()).trim();
            if (value.startsWith("\"") && value.endsWith("\"") && value.length() >= 2) {
                value = value.substring(1, value.length() - 1);
            }
            return decodeText(value);
        }
        return "";
    }

    private File uploadDir() {
        File dir = new File(filesDir, "email_compose_uploads");
        if (!dir.exists()) dir.mkdirs();
        return dir;
    }

    private File composeUploadFile(String token) {
        return new File(uploadDir(), safeFilename(valueOr(token, "")));
    }

    private byte[] readFile(File file) throws Exception {
        try (InputStream in = new FileInputStream(file)) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int n;
            while ((n = in.read(chunk)) != -1) buffer.write(chunk, 0, n);
            return buffer.toByteArray();
        }
    }

    private String safeFilename(String raw) {
        String value = valueOr(raw, "file").replace('\\', '_').replace('/', '_').trim();
        value = value.replaceAll("[^A-Za-z0-9._ -]", "_");
        while (value.startsWith(".")) value = value.substring(1);
        return value.isEmpty() ? "file" : value;
    }

    private Response summarizeEmail(Map<String, String> body) throws Exception {
        String emailBody = firstNonEmpty(body.get("body"), htmlToText(body.get("body_html")));
        if (emailBody.isEmpty()) {
            return ok(new JSONObject().put("success", false).put("error", "No body provided"));
        }
        try {
            String subject = valueOr(body.get("subject"), "");
            String sender = valueOr(body.get("from"), "");
            String prompt = "From: " + sender + "\nSubject: " + subject + "\n\n"
                    + truncate(emailBody, 12000)
                    + "\n\nSummarize this email in 1-3 short bullet points. "
                    + "Include action items, deadlines, concrete numbers, and the main point. "
                    + "Return only the bullet points.";
            LlmResult result = callConfiguredLlm(
                    "You are an email summarizer. Be terse and practical. Use '-' bullets only.",
                    prompt,
                    768,
                    valueOr(body.get("model"), "")
            );
            return ok(new JSONObject()
                    .put("success", true)
                    .put("summary", cleanModelText(result.content))
                    .put("model_used", result.model));
        } catch (Exception ex) {
            return new Response(502, new JSONObject()
                    .put("success", false)
                    .put("error", "AI summary failed: " + friendlyProviderError(ex)));
        }
    }

    private Response aiReply(Map<String, String> body) throws Exception {
        String original = valueOr(body.get("original_body"), "");
        if (original.trim().isEmpty()) {
            return ok(new JSONObject().put("success", false).put("error", "No email body provided"));
        }
        try {
            boolean fast = parseBool(body.get("fast"), false);
            String to = valueOr(body.get("to"), "");
            String subject = valueOr(body.get("subject"), "");
            String prompt = "Recipient: " + to + "\nSubject: " + subject + "\n\n"
                    + "Original email and current draft, if any:\n"
                    + truncate(original, fast ? 5000 : 9000)
                    + "\n\nDraft a helpful reply. Return only the reply body text. "
                    + "Do not include a subject line or markdown fences.";
            LlmResult result = callConfiguredLlm(
                    "You write concise, natural email replies for the user. Match the user's direct, friendly tone.",
                    prompt,
                    fast ? 768 : 1536,
                    valueOr(body.get("model"), "")
            );
            return ok(new JSONObject()
                    .put("success", true)
                    .put("reply", cleanModelText(result.content))
                    .put("model_used", result.model));
        } catch (Exception ex) {
            return new Response(502, new JSONObject()
                    .put("success", false)
                    .put("error", "AI reply failed: " + friendlyProviderError(ex)));
        }
    }

    private LlmResult callConfiguredLlm(String system, String user, int maxTokens, String requestedModel) throws Exception {
        JSONObject endpoint = resolveLlmEndpoint(requestedModel);
        String model = firstNonEmpty(resolveRequestedModel(endpoint, requestedModel), firstModel(endpoint));
        if (model.isEmpty()) throw new IllegalStateException("No model configured");
        URL url = new URL(chatUrl(endpoint.optString("base_url")));
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(LLM_TIMEOUT_MS);
        conn.setDoOutput(true);
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("Content-Type", "application/json");
        String apiKey = endpoint.optString("api_key");
        if (!apiKey.isEmpty()) conn.setRequestProperty("Authorization", "Bearer " + apiKey);
        JSONArray messages = new JSONArray()
                .put(new JSONObject().put("role", "system").put("content", system))
                .put(new JSONObject().put("role", "user").put("content", user));
        JSONObject payload = new JSONObject()
                .put("model", model)
                .put("messages", messages)
                .put("stream", false)
                .put("temperature", 0.4)
                .put("max_tokens", maxTokens);
        byte[] data = payload.toString().getBytes(StandardCharsets.UTF_8);
        conn.setFixedLengthStreamingMode(data.length);
        try (OutputStream out = conn.getOutputStream()) {
            out.write(data);
        }
        int status = conn.getResponseCode();
        String response = readAll(status >= 400 ? conn.getErrorStream() : conn.getInputStream());
        if (status < 200 || status >= 300) {
            throw new IllegalStateException("HTTP " + status + ": " + response);
        }
        String content = extractLlmContent(new JSONObject(response));
        if (content.trim().isEmpty()) throw new IllegalStateException("Model returned an empty response");
        return new LlmResult(content, model);
    }

    private JSONObject resolveLlmEndpoint(String requestedModel) throws Exception {
        JSONArray endpoints = loadArray(PREF_ENDPOINTS);
        String requested = valueOr(requestedModel, "").trim();
        if (!requested.isEmpty()) {
            for (int i = 0; i < endpoints.length(); i++) {
                JSONObject endpoint = endpoints.getJSONObject(i);
                if (!endpoint.optBoolean("is_enabled", true)) continue;
                JSONArray models = endpoint.optJSONArray("models");
                for (int m = 0; models != null && m < models.length(); m++) {
                    if (requested.equals(models.optString(m))) return endpoint;
                }
            }
        }
        String preferred = prefs.getString(PREF_DEFAULT_ENDPOINT, "");
        if (!preferred.isEmpty()) {
            for (int i = 0; i < endpoints.length(); i++) {
                JSONObject endpoint = endpoints.getJSONObject(i);
                if (preferred.equals(endpoint.optString("id")) && endpoint.optBoolean("is_enabled", true)) return endpoint;
            }
        }
        for (int i = 0; i < endpoints.length(); i++) {
            JSONObject endpoint = endpoints.getJSONObject(i);
            if (endpoint.optBoolean("is_enabled", true)) return endpoint;
        }
        throw new IllegalStateException("No LLM endpoint configured");
    }

    private String resolveRequestedModel(JSONObject endpoint, String requestedModel) {
        String requested = valueOr(requestedModel, "").trim();
        if (requested.isEmpty()) return "";
        JSONArray models = endpoint.optJSONArray("models");
        for (int i = 0; models != null && i < models.length(); i++) {
            if (requested.equals(models.optString(i))) return requested;
        }
        return "";
    }

    private String firstModel(JSONObject endpoint) {
        JSONArray models = endpoint.optJSONArray("models");
        return models != null && models.length() > 0 ? models.optString(0) : "";
    }

    private String extractLlmContent(JSONObject json) {
        JSONArray choices = json.optJSONArray("choices");
        if (choices != null && choices.length() > 0) {
            JSONObject choice = choices.optJSONObject(0);
            if (choice != null) {
                JSONObject message = choice.optJSONObject("message");
                if (message != null) return firstNonEmpty(message.optString("content"), message.optString("reasoning_content"));
                return choice.optString("text", "");
            }
        }
        JSONObject message = json.optJSONObject("message");
        if (message != null) return message.optString("content", "");
        return firstNonEmpty(json.optString("response"), json.optString("content"));
    }

    private String cleanModelText(String raw) {
        String value = valueOr(raw, "").trim();
        if (value.startsWith("```")) {
            value = value.replaceFirst("^```[a-zA-Z0-9_-]*\\s*", "");
            value = value.replaceFirst("\\s*```$", "");
        }
        value = value.replaceAll("(?is).*?<<<\\s*SUMMARY\\s*>>>", "");
        value = value.replaceAll("(?is)<<<\\s*END\\s*>>>.*", "");
        return value.trim();
    }

    private String htmlToText(String html) {
        String text = valueOr(html, "");
        if (text.trim().isEmpty()) return "";
        text = text.replaceAll("(?is)<(script|style)[^>]*>.*?</\\1>", " ");
        text = text.replaceAll("(?i)<br\\s*/?>", "\n");
        text = text.replaceAll("(?i)</(p|div|li|tr|h[1-6])>", "\n");
        text = text.replaceAll("(?is)<[^>]+>", " ");
        text = text.replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", "\"")
                .replace("&#39;", "'");
        return text.replaceAll("[ \\t\\x0B\\f\\r]+", " ")
                .replaceAll("\\n\\s*\\n+", "\n\n")
                .trim();
    }

    private String friendlyProviderError(Exception ex) {
        String msg = valueOr(ex.getMessage(), ex.getClass().getSimpleName()).replace('\n', ' ').trim();
        if (msg.length() > 260) msg = msg.substring(0, 260) + "...";
        return msg;
    }

    private String chatUrl(String baseUrl) {
        String base = normalizeBase(baseUrl);
        if (base.endsWith("/api")) return base + "/chat";
        return base + "/chat/completions";
    }

    private String normalizeBase(String raw) {
        String url = valueOr(raw, "").trim().replace('\\', '/');
        if (url.isEmpty()) return "";
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
        while (url.endsWith("/")) url = url.substring(0, url.length() - 1);
        String[] suffixes = {"/chat/completions", "/api/chat", "/completions", "/models"};
        for (String suffix : suffixes) {
            if (url.endsWith(suffix)) return url.substring(0, url.length() - suffix.length());
        }
        String lower = url.toLowerCase(Locale.US);
        if ("https://api.deepseek.com".equals(lower)
                || "https://api.openai.com".equals(lower)
                || "https://api.x.ai".equals(lower)
                || "https://api.mistral.ai".equals(lower)
                || "https://api.together.xyz".equals(lower)) {
            return url + "/v1";
        }
        return url;
    }

    private String readAll(InputStream stream) throws Exception {
        if (stream == null) return "";
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] chunk = new byte[8192];
        int n;
        while ((n = stream.read(chunk)) != -1) buffer.write(chunk, 0, n);
        return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
    }

    private String truncate(String value, int maxChars) {
        String text = valueOr(value, "");
        return text.length() <= maxChars ? text : text.substring(0, maxChars);
    }

    private void applyAccountFields(JSONObject account, Map<String, String> data, boolean create) throws Exception {
        putString(account, data, "name");
        putString(account, data, "from_address");
        putString(account, data, "imap_host");
        putInt(account, data, "imap_port", create ? 993 : account.optInt("imap_port", 993));
        putString(account, data, "imap_user");
        putPassword(account, data, "imap_password", create);
        if (data.containsKey("imap_starttls")) account.put("imap_starttls", parseBool(data.get("imap_starttls"), true));
        else if (create) account.put("imap_starttls", true);
        putString(account, data, "smtp_host");
        putInt(account, data, "smtp_port", create ? 465 : account.optInt("smtp_port", 465));
        putString(account, data, "smtp_security");
        if (!account.has("smtp_security") || account.optString("smtp_security").isEmpty()) {
            account.put("smtp_security", account.optInt("smtp_port", 465) == 587 ? "starttls" : "ssl");
        }
        putString(account, data, "smtp_user");
        putPassword(account, data, "smtp_password", create);
        if (data.containsKey("enabled")) account.put("enabled", parseBool(data.get("enabled"), true));
    }

    private JSONObject hydrateForTest(Map<String, String> data) throws Exception {
        JSONObject base = null;
        String accountId = valueOr(data.get("account_id"), "").trim();
        if (!accountId.isEmpty()) base = findAccount(accountId);
        JSONObject out = base == null ? new JSONObject() : decryptedAccountCopy(base);
        applyAccountFields(out, data, false);
        if (out.optString("smtp_user").isEmpty()) out.put("smtp_user", out.optString("imap_user"));
        if (out.optString("smtp_password").isEmpty()) out.put("smtp_password", out.optString("imap_password"));
        return out;
    }

    private JSONArray publicAccounts() throws Exception {
        JSONArray accounts = loadAccounts();
        JSONArray out = new JSONArray();
        for (int i = 0; i < accounts.length(); i++) {
            out.put(publicAccount(accounts.getJSONObject(i)));
        }
        return out;
    }

    private JSONObject publicAccount(JSONObject account) throws Exception {
        boolean smtpPasswordUsable = !account.optString("smtp_password").isEmpty()
                || (!account.optString("imap_password").isEmpty()
                    && firstNonEmpty(account.optString("smtp_user"), account.optString("imap_user"))
                        .equals(account.optString("imap_user")));
        return new JSONObject()
                .put("id", account.optString("id"))
                .put("name", account.optString("name"))
                .put("is_default", account.optBoolean("is_default", false))
                .put("enabled", account.optBoolean("enabled", true))
                .put("imap_host", account.optString("imap_host"))
                .put("imap_port", account.optInt("imap_port", 993))
                .put("imap_user", account.optString("imap_user"))
                .put("imap_starttls", account.optBoolean("imap_starttls", true))
                .put("smtp_host", account.optString("smtp_host"))
                .put("smtp_port", account.optInt("smtp_port", 465))
                .put("smtp_security", account.optString("smtp_security", account.optInt("smtp_port", 465) == 587 ? "starttls" : "ssl"))
                .put("smtp_user", account.optString("smtp_user"))
                .put("from_address", account.optString("from_address"))
                .put("has_imap_password", !account.optString("imap_password").isEmpty())
                .put("has_smtp_password", smtpPasswordUsable);
    }

    private JSONObject resolveAccount(String id) throws Exception {
        JSONObject account = valueOr(id, "").trim().isEmpty() ? defaultAccount() : findAccount(id);
        if (account == null) throw new IllegalStateException("No email account configured");
        return account;
    }

    private JSONObject resolveSendAccount(String id) throws Exception {
        JSONArray accounts = loadAccounts();
        JSONObject requested = null;
        JSONObject firstEnabled = null;
        JSONObject defaultEnabled = null;
        JSONObject firstSendable = null;
        JSONObject defaultSendable = null;
        String wanted = valueOr(id, "").trim();
        for (int i = 0; i < accounts.length(); i++) {
            JSONObject account = accounts.getJSONObject(i);
            if (!account.optBoolean("enabled", true)) continue;
            if (firstEnabled == null) firstEnabled = account;
            if (account.optBoolean("is_default", false)) defaultEnabled = account;
            if (!wanted.isEmpty() && wanted.equals(account.optString("id"))) requested = account;
            if (canSend(account)) {
                if (firstSendable == null) firstSendable = account;
                if (account.optBoolean("is_default", false)) defaultSendable = account;
            }
        }
        if (requested != null && canSend(requested)) return requested;
        if (defaultSendable != null) return defaultSendable;
        if (firstSendable != null) return firstSendable;
        if (requested != null) return requested;
        if (defaultEnabled != null) return defaultEnabled;
        return firstEnabled;
    }

    private boolean canSend(JSONObject account) throws Exception {
        return account != null
                && account.optBoolean("enabled", true)
                && !account.optString("smtp_host").trim().isEmpty()
                && !firstNonEmpty(account.optString("smtp_user"), account.optString("imap_user")).trim().isEmpty()
                && !firstNonEmpty(credential(account, "smtp_password"), credential(account, "imap_password")).trim().isEmpty();
    }

    private JSONObject defaultAccount() throws Exception {
        JSONArray accounts = loadAccounts();
        JSONObject fallback = null;
        for (int i = 0; i < accounts.length(); i++) {
            JSONObject account = accounts.getJSONObject(i);
            if (!account.optBoolean("enabled", true)) continue;
            if (fallback == null) fallback = account;
            if (account.optBoolean("is_default", false)) return account;
        }
        return fallback;
    }

    private JSONObject findAccount(String id) throws Exception {
        JSONArray accounts = loadAccounts();
        for (int i = 0; i < accounts.length(); i++) {
            JSONObject account = accounts.getJSONObject(i);
            if (id.equals(account.optString("id"))) return account;
        }
        return null;
    }

    private JSONArray loadAccounts() throws Exception {
        JSONArray accounts = loadArray(PREF_EMAIL_ACCOUNTS);
        boolean changed = false;
        for (int i = 0; i < accounts.length(); i++) {
            JSONObject account = accounts.getJSONObject(i);
            for (String key : new String[]{"imap_password", "smtp_password"}) {
                String value = account.optString(key, "");
                if (!value.isEmpty() && !value.startsWith(ENC_PREFIX)) {
                    account.put(key, encryptCredential(value));
                    changed = true;
                }
            }
            accounts.put(i, account);
        }
        if (changed) saveAccounts(accounts);
        return accounts;
    }

    private JSONArray loadArray(String key) {
        try {
            return new JSONArray(prefs.getString(key, "[]"));
        } catch (Exception ignored) {
            return new JSONArray();
        }
    }

    private void saveAccounts(JSONArray accounts) {
        prefs.edit().putString(PREF_EMAIL_ACCOUNTS, accounts.toString()).apply();
    }

    private void clearDefault(JSONArray accounts) throws Exception {
        for (int i = 0; i < accounts.length(); i++) {
            JSONObject account = accounts.getJSONObject(i);
            account.put("is_default", false);
            accounts.put(i, account);
        }
    }

    private Store openImapStore(JSONObject account) throws Exception {
        String protocol = imapProtocol(account);
        Session session = Session.getInstance(imapProperties(account, protocol));
        Store store = session.getStore(protocol);
        store.connect(
                account.optString("imap_host"),
                account.optInt("imap_port", "imaps".equals(protocol) ? 993 : 143),
                account.optString("imap_user"),
                credential(account, "imap_password")
        );
        return store;
    }

    private String imapProtocol(JSONObject account) {
        if (account.optBoolean("imap_starttls", false)) return "imap";
        return account.optInt("imap_port", 993) == 993 ? "imaps" : "imap";
    }

    private Properties imapProperties(JSONObject account, String protocol) {
        Properties props = new Properties();
        String prefix = "mail." + protocol + ".";
        props.put(prefix + "connectiontimeout", String.valueOf(MAIL_TIMEOUT_MS));
        props.put(prefix + "timeout", String.valueOf(MAIL_TIMEOUT_MS));
        props.put(prefix + "writetimeout", String.valueOf(MAIL_TIMEOUT_MS));
        if ("imap".equals(protocol) && account.optBoolean("imap_starttls", false)) {
            props.put("mail.imap.starttls.enable", "true");
        }
        if ("imaps".equals(protocol)) props.put("mail.imaps.ssl.enable", "true");
        return props;
    }

    private Transport openSmtpTransport(JSONObject account) throws Exception {
        Session session = Session.getInstance(smtpProperties(account));
        Transport transport = session.getTransport("smtp");
        transport.connect(
                account.optString("smtp_host"),
                account.optInt("smtp_port", 465),
                firstNonEmpty(account.optString("smtp_user"), account.optString("imap_user")),
                firstNonEmpty(credential(account, "smtp_password"), credential(account, "imap_password"))
        );
        return transport;
    }

    private Properties smtpProperties(JSONObject account) {
        Properties props = new Properties();
        props.put("mail.smtp.auth", "true");
        props.put("mail.smtp.connectiontimeout", String.valueOf(MAIL_TIMEOUT_MS));
        props.put("mail.smtp.timeout", String.valueOf(MAIL_TIMEOUT_MS));
        props.put("mail.smtp.writetimeout", String.valueOf(MAIL_TIMEOUT_MS));
        String security = account.optString("smtp_security", account.optInt("smtp_port", 465) == 587 ? "starttls" : "ssl");
        if ("ssl".equalsIgnoreCase(security)) {
            props.put("mail.smtp.ssl.enable", "true");
        } else if ("starttls".equalsIgnoreCase(security)) {
            props.put("mail.smtp.starttls.enable", "true");
        }
        return props;
    }

    private Folder openMailFolder(Store store, String name, int mode) throws Exception {
        Folder folder = store.getFolder(valueOr(name, "INBOX"));
        if (!folder.exists() && "INBOX".equalsIgnoreCase(name)) folder = store.getFolder("INBOX");
        if (!folder.exists()) throw new MessagingException("Folder not found: " + name);
        folder.open(mode);
        return folder;
    }

    private void collectFolders(Folder parent, List<String> out) throws MessagingException {
        Folder[] folders = parent.list("*");
        for (Folder folder : folders) {
            if ((folder.getType() & Folder.HOLDS_MESSAGES) != 0) out.add(folder.getFullName());
        }
    }

    private JSONObject messageSummary(Message message, UIDFolder uidFolder) throws Exception {
        AddressInfo from = firstAddress(message.getFrom());
        Date date = firstNonNull(message.getSentDate(), message.getReceivedDate());
        Flags flags = message.getFlags();
        return new JSONObject()
                .put("uid", String.valueOf(uidFolder.getUID(message)))
                .put("message_id", header(message, "Message-ID"))
                .put("subject", valueOr(message.getSubject(), "(no subject)"))
                .put("from_name", firstNonEmpty(from.name, from.address))
                .put("from_address", from.address)
                .put("to", addressesToString(message.getRecipients(Message.RecipientType.TO)))
                .put("cc", addressesToString(message.getRecipients(Message.RecipientType.CC)))
                .put("date", isoDate(date))
                .put("date_display", date == null ? "" : date.toString())
                .put("date_epoch", date == null ? 0.0 : date.getTime() / 1000.0)
                .put("size", Math.max(0, message.getSize()))
                .put("is_read", flags.contains(Flags.Flag.SEEN))
                .put("is_answered", flags.contains(Flags.Flag.ANSWERED))
                .put("is_flagged", flags.contains(Flags.Flag.FLAGGED))
                .put("flags", flagsJson(flags))
                .put("has_attachments", maybeHasAttachments(message))
                .put("tags", new JSONArray())
                .put("is_spam_verdict", false);
    }

    private JSONObject messageDetail(Message message, UIDFolder uidFolder, String folderName) throws Exception {
        JSONObject summary = messageSummary(message, uidFolder);
        BodyParts parts = new BodyParts();
        collectBodyParts(message, parts);
        return new JSONObject(summary.toString())
                .put("folder", folderName)
                .put("in_reply_to", header(message, "In-Reply-To"))
                .put("references", header(message, "References"))
                .put("body", parts.text)
                .put("body_html", parts.html)
                .put("attachments", parts.attachments)
                .put("cached_summary", JSONObject.NULL)
                .put("cached_ai_reply", JSONObject.NULL)
                .put("boundaries", JSONObject.NULL)
                .put("thread_turns", JSONObject.NULL)
                .put("sender_signature", JSONObject.NULL);
    }

    private void collectBodyParts(Part part, BodyParts out) throws Exception {
        String disposition = part.getDisposition();
        String filename = decodeText(part.getFileName());
        boolean attachment = Part.ATTACHMENT.equalsIgnoreCase(valueOr(disposition, "")) || !filename.isEmpty();
        if (attachment) {
            out.attachments.put(new JSONObject()
                    .put("index", out.attachments.length())
                    .put("filename", firstNonEmpty(filename, "attachment"))
                    .put("content_type", valueOr(part.getContentType(), "application/octet-stream"))
                    .put("size", Math.max(0, part.getSize())));
            return;
        }
        if (part.isMimeType("text/plain")) {
            if (out.text.isEmpty()) out.text = String.valueOf(part.getContent());
            return;
        }
        if (part.isMimeType("text/html")) {
            if (out.html.isEmpty()) out.html = String.valueOf(part.getContent());
            return;
        }
        Object content = part.getContent();
        if (content instanceof Multipart) {
            Multipart multipart = (Multipart) content;
            for (int i = 0; i < multipart.getCount(); i++) {
                BodyPart child = multipart.getBodyPart(i);
                collectBodyParts(child, out);
            }
        } else if (content instanceof Message) {
            collectBodyParts((Message) content, out);
        }
    }

    private String appendSent(JSONObject account, MimeMessage message) {
        Store store = null;
        Folder folder = null;
        try {
            store = openImapStore(account);
            String sent = detectSentFolderName(store);
            if (sent == null) return null;
            folder = store.getFolder(sent);
            if (!folder.exists()) folder.create(Folder.HOLDS_MESSAGES);
            folder.open(Folder.READ_WRITE);
            folder.appendMessages(new Message[]{message});
            return sent;
        } catch (Exception ignored) {
            return null;
        } finally {
            safeClose(folder, false);
            safeClose(store);
        }
    }

    private String detectArchiveFolderName(Map<String, String> query) {
        Store store = null;
        try {
            store = openImapStore(resolveAccount(query.get("account_id")));
            List<String> names = new ArrayList<>();
            collectFolders(store.getDefaultFolder(), names);
            String[] preferred = {"[Gmail]/All Mail", "All Mail", "Archive", "Archives"};
            for (String p : preferred) {
                for (String n : names) if (p.equalsIgnoreCase(n)) return n;
            }
        } catch (Exception ignored) {
        } finally {
            safeClose(store);
        }
        return "";
    }

    private String detectSentFolderName(Store store) throws MessagingException {
        List<String> names = new ArrayList<>();
        collectFolders(store.getDefaultFolder(), names);
        String[] preferred = {"Sent", "Sent Mail", "[Gmail]/Sent Mail", "INBOX.Sent", "Sent Items"};
        for (String p : preferred) {
            for (String n : names) if (p.equalsIgnoreCase(n)) return n;
        }
        for (String n : names) if (n.toLowerCase(Locale.US).contains("sent")) return n;
        return names.contains("Sent") ? "Sent" : null;
    }

    private void addRecipients(MimeMessage message, Message.RecipientType type, String raw) throws Exception {
        String value = valueOr(raw, "").trim();
        if (value.isEmpty() || "null".equalsIgnoreCase(value)) return;
        message.setRecipients(type, InternetAddress.parse(value, false));
    }

    private void putString(JSONObject obj, Map<String, String> data, String key) throws Exception {
        if (data.containsKey(key)) obj.put(key, valueOr(data.get(key), "").trim());
    }

    private void putPassword(JSONObject obj, Map<String, String> data, String key, boolean create) throws Exception {
        if (data.containsKey(key) && !valueOr(data.get(key), "").isEmpty()) obj.put(key, encryptCredential(data.get(key)));
        else if (create && !obj.has(key)) obj.put(key, "");
    }

    private void putInt(JSONObject obj, Map<String, String> data, String key, int fallback) throws Exception {
        if (data.containsKey(key)) obj.put(key, parseInt(data.get(key), fallback));
        else if (!obj.has(key)) obj.put(key, fallback);
    }

    private JSONObject decryptedAccountCopy(JSONObject account) throws Exception {
        JSONObject copy = new JSONObject(account.toString());
        copy.put("imap_password", credential(account, "imap_password"));
        copy.put("smtp_password", credential(account, "smtp_password"));
        return copy;
    }

    private String credential(JSONObject account, String key) throws Exception {
        String stored = account.optString(key, "");
        if (stored.isEmpty()) return "";
        if (!stored.startsWith(ENC_PREFIX)) return stored;
        return decryptCredential(stored);
    }

    private String encryptCredential(String raw) throws Exception {
        String value = valueOr(raw, "");
        if (value.isEmpty()) return "";
        if (value.startsWith(ENC_PREFIX)) return value;
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, emailSecretKey());
        byte[] encrypted = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
        String iv = Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP);
        String payload = Base64.encodeToString(encrypted, Base64.NO_WRAP);
        return ENC_PREFIX + iv + ":" + payload;
    }

    private String decryptCredential(String stored) throws Exception {
        if (!stored.startsWith(ENC_PREFIX)) return stored;
        String payload = stored.substring(ENC_PREFIX.length());
        int sep = payload.indexOf(':');
        if (sep <= 0) return "";
        byte[] iv = Base64.decode(payload.substring(0, sep), Base64.NO_WRAP);
        byte[] encrypted = Base64.decode(payload.substring(sep + 1), Base64.NO_WRAP);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, emailSecretKey(), new GCMParameterSpec(GCM_TAG_BITS, iv));
        return new String(cipher.doFinal(encrypted), StandardCharsets.UTF_8);
    }

    private SecretKey emailSecretKey() throws Exception {
        KeyStore keyStore = KeyStore.getInstance("AndroidKeyStore");
        keyStore.load(null);
        if (!keyStore.containsAlias(KEY_ALIAS)) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setRandomizedEncryptionRequired(true)
                    .build());
            generator.generateKey();
        }
        KeyStore.SecretKeyEntry entry = (KeyStore.SecretKeyEntry) keyStore.getEntry(KEY_ALIAS, null);
        return entry.getSecretKey();
    }

    private String header(Message message, String name) throws MessagingException {
        String[] values = message.getHeader(name);
        return values == null || values.length == 0 ? "" : valueOr(values[0], "").trim();
    }

    private JSONArray flagsJson(Flags flags) {
        JSONArray out = new JSONArray();
        if (flags.contains(Flags.Flag.SEEN)) out.put("\\Seen");
        if (flags.contains(Flags.Flag.ANSWERED)) out.put("\\Answered");
        if (flags.contains(Flags.Flag.FLAGGED)) out.put("\\Flagged");
        if (flags.contains(Flags.Flag.DELETED)) out.put("\\Deleted");
        return out;
    }

    private boolean maybeHasAttachments(Part part) {
        try {
            String ct = valueOr(part.getContentType(), "").toLowerCase(Locale.US);
            return ct.contains("multipart/mixed") || ct.contains("multipart/related") || !valueOr(part.getFileName(), "").isEmpty();
        } catch (Exception ignored) {
            return false;
        }
    }

    private AddressInfo firstAddress(Address[] addresses) {
        if (addresses == null || addresses.length == 0) return new AddressInfo("", "");
        Address address = addresses[0];
        if (address instanceof InternetAddress) {
            InternetAddress ia = (InternetAddress) address;
            return new AddressInfo(valueOr(ia.getPersonal(), ""), valueOr(ia.getAddress(), ""));
        }
        return new AddressInfo("", address.toString());
    }

    private String addressesToString(Address[] addresses) {
        if (addresses == null || addresses.length == 0) return "";
        try {
            return InternetAddress.toString(addresses);
        } catch (Exception ignored) {
            StringBuilder out = new StringBuilder();
            for (Address address : addresses) {
                if (out.length() > 0) out.append(", ");
                out.append(address.toString());
            }
            return out.toString();
        }
    }

    private String decodeText(String value) {
        try {
            return value == null ? "" : MimeUtility.decodeText(value);
        } catch (Exception ignored) {
            return valueOr(value, "");
        }
    }

    private String friendlyMailError(String protocol, Exception ex) {
        String msg = valueOr(ex.getMessage(), ex.getClass().getSimpleName()).replace('\n', ' ').trim();
        if (msg.length() > 240) msg = msg.substring(0, 240) + "...";
        return protocol + " failed: " + msg;
    }

    private String isoDate(Date date) {
        if (date == null) return "";
        SimpleDateFormat fmt = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US);
        fmt.setTimeZone(TimeZone.getTimeZone("UTC"));
        return fmt.format(date);
    }

    private void safeClose(Store store) {
        if (store == null) return;
        try {
            store.close();
        } catch (Exception ignored) {}
    }

    private void safeClose(Folder folder, boolean expunge) {
        if (folder == null) return;
        try {
            if (folder.isOpen()) folder.close(expunge);
        } catch (Exception ignored) {}
    }

    private String firstNonEmpty(String... values) {
        for (String value : values) {
            if (value != null && !value.trim().isEmpty() && !"null".equalsIgnoreCase(value.trim())) return value.trim();
        }
        return "";
    }

    private Date firstNonNull(Date a, Date b) {
        return a != null ? a : b;
    }

    private int parseInt(String raw, int fallback) {
        try {
            return Integer.parseInt(valueOr(raw, "").trim());
        } catch (Exception ignored) {
            return fallback;
        }
    }

    private long parseLong(String raw, long fallback) {
        try {
            return Long.parseLong(valueOr(raw, "").trim());
        } catch (Exception ignored) {
            return fallback;
        }
    }

    private boolean parseBool(String raw, boolean fallback) {
        if (raw == null) return fallback;
        String v = raw.trim().toLowerCase(Locale.US);
        if ("true".equals(v) || "1".equals(v) || "yes".equals(v) || "on".equals(v)) return true;
        if ("false".equals(v) || "0".equals(v) || "no".equals(v) || "off".equals(v)) return false;
        return fallback;
    }

    private int clamp(int value, int min, int max) {
        return Math.max(min, Math.min(max, value));
    }

    private String valueOr(String value, String fallback) {
        return value == null ? fallback : value;
    }

    private static class AddressInfo {
        final String name;
        final String address;

        AddressInfo(String name, String address) {
            this.name = name;
            this.address = address;
        }
    }

    private static class BodyParts {
        String text = "";
        String html = "";
        JSONArray attachments = new JSONArray();
    }

    private static class UploadPart {
        final String filename;
        final byte[] data;

        UploadPart(String filename, byte[] data) {
            this.filename = filename;
            this.data = data;
        }
    }

    private static class LlmResult {
        final String content;
        final String model;

        LlmResult(String content, String model) {
            this.content = content;
            this.model = model;
        }
    }
}
