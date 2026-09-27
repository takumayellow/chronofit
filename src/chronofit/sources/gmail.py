"""Gmail から決済の通知メールを読む（読み取り専用）。

- 権限は `gmail.readonly` だけを求める。送信・削除・ラベル変更はできない
- 検索は git の外の `sources.json` に登録した送信元と件名に限る。他のメールは取りに行かない
- 更新トークンは git の外に置き、Windows では DPAPI（ログオン中のユーザーだけが
  復号できる）で暗号化する
- Google のライブラリはこの機能を使うときだけ読み込む（他の機能は標準ライブラリだけで動く）
"""
import base64
import ctypes
import json
import sys

from . import payments

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_FILE = "gmail-token.bin"
MAX_MESSAGES = 2000


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data, protect):
    """DPAPI で暗号化 / 復号する。ログオン中のユーザー以外は復号できない。"""
    if sys.platform != "win32":
        raise OSError("トークンの暗号化は Windows（DPAPI）でだけ対応している")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    source = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = _Blob(len(data), source), _Blob()
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not call(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):
        raise OSError("DPAPI での暗号化/復号に失敗した")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def save_token(root, credentials_json, name=TOKEN_FILE):
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(_dpapi(credentials_json.encode("utf-8"), True))


def load_token(root, name=TOKEN_FILE):
    path = root / name
    if not path.is_file():
        return None
    return _dpapi(path.read_bytes(), False).decode("utf-8")


def authorize(root, client_secret, scopes=SCOPES, name=TOKEN_FILE):
    """ブラウザで1回だけ同意してもらい、更新トークンを保存する。"""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), scopes)
    credentials = flow.run_local_server(port=0, open_browser=True,
                                        authorization_prompt_message="",
                                        success_message="chronofit: 同意を受け取った。"
                                                        "このタブは閉じてよい。")
    save_token(root, credentials.to_json(), name)
    return True


def build_service(root, api, version, scopes, name, hint):
    """保存したトークンで API の窓口を作る。期限切れなら更新して保存し直す。"""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    stored = load_token(root, name)
    if not stored:
        raise RuntimeError(f"先に `{hint}` で同意する")
    credentials = Credentials.from_authorized_user_info(json.loads(stored), scopes)
    if not credentials.valid:
        credentials.refresh(Request())
        save_token(root, credentials.to_json(), name)
    return build(api, version, credentials=credentials, cache_discovery=False)


def _service(root):
    return build_service(root, "gmail", "v1", SCOPES, TOKEN_FILE, "chronofit payments auth")


def _decode(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def body_text(payload):
    """MIME の本文を平文で。text/plain があればそれ、無ければ HTML を平文にする。"""
    plain, html = [], []

    def walk(part):
        mime, data = part.get("mimeType", ""), (part.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            html.append(_decode(data))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    if plain:
        return "\n".join(plain)
    return "\n".join(payments.html_to_text(page) for page in html)


def _header(payload, name):
    for header in payload.get("headers") or []:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def fetch(root, since=None, known_ids=()):
    """対象メールを読み、決済の行を返す。既に持っているメッセージ ID は取りに行かない。"""
    sources = payments.load_sources(root)
    if not sources:
        raise RuntimeError("読む通知が未登録（`chronofit payments source` で登録する）")
    service = _service(root)
    rows, skipped = [], 0
    for query in payments.gmail_queries(sources):
        if since:
            query += f" after:{since.replace('-', '/')}"
        request = service.users().messages().list(userId="me", q=query, maxResults=500)
        ids = []
        while request is not None and len(ids) < MAX_MESSAGES:
            response = request.execute()
            ids += [m["id"] for m in response.get("messages", [])]
            request = service.users().messages().list_next(request, response)
        for message_id in ids:
            if message_id in known_ids:
                continue
            message = service.users().messages().get(userId="me", id=message_id,
                                                      format="full").execute()
            payload = message.get("payload") or {}
            row = payments.parse_message(_header(payload, "From"), _header(payload, "Subject"),
                                         body_text(payload), sources)
            if row is None:
                skipped += 1
                continue
            rows.append({"id": message_id, **row})
    return rows, skipped
