"""Network-free startup ping regressions: bot identity, delivery, and silence on errors."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch, Mock

import requests
from telegram_startup_probe import verify_and_ping


class TelegramStartupProbeTests(unittest.TestCase):
    def response(self, username="CryptoMarketState_bot", status_code=200, ok=True):
        return Mock(status_code=status_code,
                    json=Mock(return_value={
                        "ok": ok, "result": {"username": username}
                    }))

    def test_verified_telegram_identity_and_accepted_ping(self):
        calls=[]
        def send(msg):
            calls.append(msg)
            return True
        log=io.StringIO()
        with patch("telegram_startup_probe.requests.get",
                   return_value=self.response()) as get, redirect_stdout(log):
            ok=verify_and_ping("NONSECRET_FAKE_TOKEN", send, "CONFIRMED_ONLY")
        self.assertTrue(ok)
        self.assertEqual(len(calls),1)
        self.assertIn("ПРОВЕРКА СВЯЗИ",calls[0])
        self.assertIn("\n",calls[0])
        self.assertIn("НЕ торговый сигнал",calls[0])
        self.assertIn("CONFIRMED_ONLY",calls[0])
        self.assertEqual(get.call_args.kwargs["timeout"],8)
        self.assertIn("/getMe",get.call_args.args[0])
        self.assertIn("status=ACCEPTED",log.getvalue())
        self.assertNotIn("NONSECRET_FAKE_TOKEN",log.getvalue())

    def test_wrong_telegram_bot_must_not_receive_message(self):
        sent=[]
        log=io.StringIO()
        with patch("telegram_startup_probe.requests.get",
                   return_value=self.response(username="Other_bot")), redirect_stdout(log):
            ok=verify_and_ping("FAKE_TOKEN",lambda m:sent.append(m),"CONFIRMED_ONLY")
        self.assertFalse(ok)
        self.assertEqual(sent,[])
        self.assertIn("BOT_USERNAME_MISMATCH",log.getvalue())
        self.assertNotIn("FAKE_TOKEN",log.getvalue())

    def test_network_and_rejected_getme_fail_closed(self):
        for side_effect,response in (
                (requests.Timeout,None),
                (None,self.response(status_code=401)),
                (None,self.response(ok=False)),
                (None,Mock(status_code=200,
                           json=Mock(side_effect=ValueError("bad json"))))):
            with self.subTest(side_effect=side_effect,response=response):
                send=Mock()
                with patch("telegram_startup_probe.requests.get",
                           side_effect=side_effect, return_value=response):
                    self.assertFalse(verify_and_ping(
                        "FAKE_TOKEN",send,"CONFIRMED_ONLY"))
                send.assert_not_called()

    def test_unacknowledged_send_is_not_reported_delivered(self):
        log=io.StringIO()
        with patch("telegram_startup_probe.requests.get",
                   return_value=self.response()), redirect_stdout(log):
            self.assertFalse(verify_and_ping(
                "FAKE_TOKEN",lambda x: False,"CONFIRMED_ONLY"))
        self.assertIn("status=FAILED",log.getvalue())
        self.assertNotIn("status=ACCEPTED",log.getvalue())

    def test_missing_token_never_calls_telegram(self):
        with patch("telegram_startup_probe.requests.get") as get:
            self.assertFalse(verify_and_ping(
                "", lambda m: self.fail("send called"), "CONFIRMED_ONLY"))
        get.assert_not_called()


if __name__=="__main__":
    unittest.main()
