import httpx

from scanner import telegram_setup as ts

TOKEN = "123456789:AAH" + "x" * 32


def fake_client(sent):
    def handler(req: httpx.Request):
        path = req.url.path
        if path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "my_props_bot"}})
        if path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True})
        if path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 5, "message": {"chat": {"id": 987654, "type": "private", "first_name": "J"}}}]})
        if path.endswith("/sendMessage"):
            sent.append(req.content.decode())
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_setup_writes_env_and_sends_test(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# keep me\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\nMIN_NET_EDGE=0.04\n")
    sent = []
    assert ts.run(fake_client(sent), env, input_fn=lambda _: TOKEN) == 0
    text = env.read_text()
    assert f"TELEGRAM_BOT_TOKEN={TOKEN}" in text and "TELEGRAM_CHAT_ID=987654" in text
    assert "MIN_NET_EDGE=0.04" in text and "# keep me" in text
    assert oct(env.stat().st_mode)[-3:] == "600"
    assert sent and "987654" in sent[0]


def test_bad_token_saves_nothing(tmp_path):
    env = tmp_path / ".env"
    assert ts.run(fake_client([]), env, input_fn=lambda _: "not-a-token") == 1
    assert not env.exists()
