"""A tiny SMTP server for tests: speaks just enough SMTP to receive a message, and can be told to fail."""
import asyncio
import base64


class FakeSMTP:
    def __init__(self, reject_auth=False, drop_after_data=False, require_login=False):
        self.messages: list[dict] = []
        self.reject_auth, self.drop_after_data, self.require_login = reject_auth, drop_after_data, require_login
        self.logins: list[tuple[str, str]] = []
        self.server = None
        self.port = 0

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def _client(self, reader, writer):
        def send(line: str) -> None:
            writer.write(line.encode() + b"\r\n")

        send("220 fake ESMTP")
        mail_from, rcpt, authed = "", [], not self.require_login
        try:
            while line := await reader.readline():
                cmd = line.decode().strip()
                upper = cmd.upper()
                if upper.startswith("EHLO"):
                    send("250-fake")
                    send("250 AUTH PLAIN LOGIN")
                elif upper.startswith("HELO"):
                    send("250 fake")
                elif upper.startswith("AUTH PLAIN"):
                    if self.reject_auth:
                        send("535 5.7.8 bad credentials")
                    else:
                        _, user, password = base64.b64decode(cmd.split()[2]).split(b"\0")
                        self.logins.append((user.decode(), password.decode()))
                        authed = True
                        send("235 ok")
                elif upper.startswith("MAIL FROM"):
                    mail_from, rcpt = cmd.split(":", 1)[1].strip(" <>"), []
                    send("250 ok")
                elif upper.startswith("RCPT TO"):
                    rcpt.append(cmd.split(":", 1)[1].strip(" <>"))
                    send("250 ok")
                elif upper == "DATA":
                    if not authed:
                        send("530 authentication required")
                        continue
                    send("354 go")
                    lines = []
                    while (data := (await reader.readline()).decode()) != ".\r\n":
                        lines.append(data[1:] if data.startswith("..") else data)
                    if self.drop_after_data:
                        writer.close()
                        return
                    self.messages.append({"from": mail_from, "to": list(rcpt), "data": "".join(lines)})
                    send("250 queued")
                elif upper == "QUIT":
                    send("221 bye")
                    break
                else:
                    send("250 ok")
                await writer.drain()
            await writer.drain()
        finally:
            writer.close()
