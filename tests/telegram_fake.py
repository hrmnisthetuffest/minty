"""A fake Telegram Bot API server for tests: httpx.MockTransport with state."""
import json
from email.parser import BytesParser
from email.policy import default as _default_policy

import httpx

TOKEN = "123456:TEST-token-not-real"


class FakeTelegram:
    def __init__(self):
        self.updates: list[dict] = []
        self.sent: list[dict] = []  # sendMessage params, in order
        self.edits: list[dict] = []
        self.actions: list[dict] = []
        self.answers: list[dict] = []  # answerCallbackQuery
        self.commands: list[dict] = []
        self.polls: list[dict] = []
        self.media: list[dict] = []  # sendVoice/sendPhoto/sendDocument uploads: method, chat_id, filename, data, caption
        self.inline_answers: list[dict] = []
        self.files: dict[str, tuple[str, bytes]] = {}  # file_id -> (file_path, bytes)
        self.calls: list[str] = []
        self.fail_once: dict[str, dict] = {}  # method -> a response body to return once instead
        self._next_update = 1
        self._next_message = 100
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def add_file(self, file_id: str, data: bytes, path: str = "") -> None:
        self.files[file_id] = (path or f"files/{file_id}", data)

    @staticmethod
    def _multipart(content_type: str, body: bytes) -> tuple[dict, dict]:
        message = BytesParser(policy=_default_policy).parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
        fields, files = {}, {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename is None:
                fields[name] = payload.decode()
            else:
                files[name] = (filename, payload)
        return fields, files

    # ---- feeding updates ------------------------------------------------------------------------
    def say(self, chat_id: int, text: str, **extra) -> dict:
        msg = {"message_id": self._next_message, "chat": {"id": chat_id, "type": "private"},
               "from": {"id": chat_id, "first_name": "Alex"}, "text": text, **extra}
        self._next_message += 1
        return self._push({"message": msg})

    def press(self, chat_id: int, data: str, message_id: int = 1) -> dict:
        return self._push({"callback_query": {"id": f"cb{self._next_update}", "from": {"id": chat_id},
                                              "message": {"message_id": message_id, "chat": {"id": chat_id}},
                                              "data": data}})

    def vote(self, chat_id: int, poll_id: str, option_ids: list[int]) -> dict:
        return self._push({"poll_answer": {"poll_id": poll_id, "user": {"id": chat_id}, "option_ids": option_ids}})

    def _push(self, body: dict) -> dict:
        update = {"update_id": self._next_update, **body}
        self._next_update += 1
        self.updates.append(update)
        return update

    # ---- the API ---------------------------------------------------------------------------------
    def texts(self, chat_id: int | None = None) -> list[str]:
        return [m["text"] for m in self.sent if chat_id is None or m["chat_id"] == chat_id]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert f"/bot{TOKEN}/" in path, "the token must be in the URL"
        if request.method == "GET" and f"/file/bot{TOKEN}/" in path:
            wanted = path.split(f"/file/bot{TOKEN}/", 1)[1]
            for file_path, data in self.files.values():
                if file_path == wanted:
                    return httpx.Response(200, content=data)
            return httpx.Response(404, json={"ok": False, "error_code": 404, "description": "Not Found"})
        method = path.rsplit("/", 1)[-1]
        ctype = request.headers.get("content-type", "")
        files: dict = {}
        if ctype.startswith("multipart/"):
            params, files = self._multipart(ctype, request.content)
            if "chat_id" in params:
                params["chat_id"] = int(params["chat_id"])
        else:
            params = json.loads(request.content or b"{}")
        self.calls.append(method)
        if method in self.fail_once:
            spec = self.fail_once.pop(method)
            body = {k: v for k, v in spec.items() if k != "status"} or {
                "ok": False, "error_code": 429, "description": "Too Many Requests: retry after 1",
                "parameters": {"retry_after": 1}}
            return httpx.Response(spec["status"], json=body)
        if method == "getMe":
            return self._ok({"id": 999, "is_bot": True, "username": "minty_test_bot"})
        if method == "getFile":
            entry = self.files.get(params.get("file_id"))
            if entry is None:
                return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "Bad Request: wrong file_id"})
            return self._ok({"file_id": params["file_id"], "file_path": entry[0], "file_size": len(entry[1])})
        if method in ("sendVoice", "sendPhoto", "sendDocument", "sendVideo"):
            name, (filename, data) = next(iter(files.items()))
            self.media.append({"method": method, "chat_id": params.get("chat_id"), "filename": filename, "data": data,
                               "caption": params.get("caption"), "field": name})
            return self._ok({"message_id": self._next_message_id(), "chat": {"id": params.get("chat_id")}})
        if method == "answerInlineQuery":
            self.inline_answers.append(params)
            return self._ok(True)
        if method == "getUpdates":
            offset = params.get("offset") or 0
            fresh = [u for u in self.updates if u["update_id"] >= offset]
            return self._ok(fresh)
        if method == "sendMessage":
            self.sent.append(params)
            return self._ok({"message_id": self._next_message_id(), "chat": {"id": params["chat_id"]},
                             "text": params["text"]})
        if method == "editMessageText":
            self.edits.append(params)
            return self._ok({"message_id": params["message_id"], "chat": {"id": params["chat_id"]}})
        if method == "sendChatAction":
            self.actions.append(params)
            return self._ok(True)
        if method == "answerCallbackQuery":
            self.answers.append(params)
            return self._ok(True)
        if method == "setMyCommands":
            self.commands = params["commands"]
            return self._ok(True)
        if method == "sendPoll":
            self.polls.append(params)
            return self._ok({"message_id": self._next_message_id(), "poll": {"id": f"poll{len(self.polls)}"}})
        return httpx.Response(404, json={"ok": False, "error_code": 404, "description": "Not Found"})

    def _next_message_id(self) -> int:
        self._next_message += 1
        return self._next_message

    @staticmethod
    def _ok(result) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": result})
