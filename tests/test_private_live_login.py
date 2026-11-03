import contextlib
import io
import unittest
from unittest.mock import patch
import private_live_login as login


class Tests(unittest.TestCase):
    def test_one_session_is_private_and_not_saved(self):
        output=io.StringIO()
        values=['fixture-api','fixture-client','fixture-pin','123456']
        with patch('private_live_login.sys.stdin.isatty',return_value=True),patch('private_live_login.os.path.isfile',return_value=False),patch('private_live_login.hidden',side_effect=values),patch('private_live_login.network_details',return_value=('mac','local','public')),patch('private_live_login.authenticate_session',return_value=({'X-PrivateKey':'fixture-api'},{'jwtToken':'fixture-jwt','feedToken':'fixture-feed'})),contextlib.redirect_stdout(output):
            session=login.one_session()
        self.assertEqual(session['x-feed-token'],'fixture-feed')
        for value in values+['fixture-jwt','fixture-feed']: self.assertNotIn(value,output.getvalue())
        self.assertFalse(hasattr(login,'save'))
    def test_redirected_input_never_prompts(self):
        with patch('private_live_login.sys.stdin.isatty',return_value=False),patch('private_live_login.hidden') as prompt:
            with self.assertRaises(RuntimeError): login.one_session()
            prompt.assert_not_called()

if __name__=='__main__': unittest.main()
