# This file is part of cloud-init. See LICENSE file for license information.

import logging

import httpretty
import pytest
import requests

from cloudinit import util, version
from cloudinit.url_helper import (
    NOT_FOUND,
    REDACTED,
    UrlError,
    oauth_headers,
    read_file_or_url,
    retry_on_url_exc,
)
from tests.unittests.helpers import CiTestCase, mock, skipIf

try:
    import oauthlib

    assert oauthlib  # avoid pyflakes error F401: import unused
    _missing_oauthlib_dep = False
except ImportError:
    _missing_oauthlib_dep = True


M_PATH = "cloudinit.url_helper."


class TestOAuthHeaders(CiTestCase):
    def test_oauth_headers_raises_not_implemented_when_oathlib_missing(self):
        """oauth_headers raises a NotImplemented error when oauth absent."""
        with mock.patch.dict("sys.modules", {"oauthlib": None}):
            with self.assertRaises(NotImplementedError) as context_manager:
                oauth_headers(1, 2, 3, 4, 5)
        self.assertEqual(
            "oauth support is not available", str(context_manager.exception)
        )

    @skipIf(_missing_oauthlib_dep, "No python-oauthlib dependency")
    @mock.patch("oauthlib.oauth1.Client")
    def test_oauth_headers_calls_oathlibclient_when_available(self, m_client):
        """oauth_headers calls oaut1.hClient.sign with the provided url."""

        class fakeclient(object):
            def sign(self, url):
                # The first and 3rd item of the client.sign tuple are ignored
                return ("junk", url, "junk2")

        m_client.return_value = fakeclient()

        return_value = oauth_headers(
            "url",
            "consumer_key",
            "token_key",
            "token_secret",
            "consumer_secret",
        )
        self.assertEqual("url", return_value)


class TestReadFileOrUrl(CiTestCase):

    with_logs = True

    def test_read_file_or_url_str_from_file(self):
        """Test that str(result.contents) on file is text version of contents.
        It should not be "b'data'", but just "'data'" """
        tmpf = self.tmp_path("myfile1")
        data = b"This is my file content\n"
        util.write_file(tmpf, data, omode="wb")
        result = read_file_or_url("file://%s" % tmpf)
        self.assertEqual(result.contents, data)
        self.assertEqual(str(result), data.decode("utf-8"))

    @httpretty.activate
    def test_read_file_or_url_str_from_url(self):
        """Test that str(result.contents) on url is text version of contents.
        It should not be "b'data'", but just "'data'" """
        url = "http://hostname/path"
        data = b"This is my url content\n"
        httpretty.register_uri(httpretty.GET, url, data)
        result = read_file_or_url(url)
        self.assertEqual(result.contents, data)
        self.assertEqual(str(result), data.decode("utf-8"))

    @httpretty.activate
    def test_read_file_or_url_str_from_url_redacting_headers_from_logs(self):
        """Headers are redacted from logs but unredacted in requests."""
        url = "http://hostname/path"
        headers = {"sensitive": "sekret", "server": "blah"}
        httpretty.register_uri(httpretty.GET, url)
        # By default, httpretty will log our request along with the header,
        # so if we don't change this the secret will show up in the logs
        logging.getLogger("httpretty.core").setLevel(logging.CRITICAL)

        read_file_or_url(url, headers=headers, headers_redact=["sensitive"])
        logs = self.logs.getvalue()
        for k in headers.keys():
            self.assertEqual(headers[k], httpretty.last_request().headers[k])
        self.assertIn(REDACTED, logs)
        self.assertNotIn("sekret", logs)

    @httpretty.activate
    def test_read_file_or_url_str_from_url_redacts_noheaders(self):
        """When no headers_redact, header values are in logs and requests."""
        url = "http://hostname/path"
        headers = {"sensitive": "sekret", "server": "blah"}
        httpretty.register_uri(httpretty.GET, url)

        read_file_or_url(url, headers=headers)
        for k in headers.keys():
            self.assertEqual(headers[k], httpretty.last_request().headers[k])
        logs = self.logs.getvalue()
        self.assertNotIn(REDACTED, logs)
        self.assertIn("sekret", logs)

    def test_wb_read_url_defaults_honored_by_read_file_or_url_callers(self):
        """Readurl param defaults used when unspecified by read_file_or_url

        Param defaults tested are as follows:
            retries: 0, additional headers None beyond default, method: GET,
            data: None, check_status: True and allow_redirects: True
        """
        url = "http://hostname/path"

        m_response = mock.MagicMock()

        class FakeSession(requests.Session):
            @classmethod
            def request(cls, **kwargs):
                self.assertEqual(
                    {
                        "url": url,
                        "allow_redirects": True,
                        "method": "GET",
                        "headers": {
                            "User-Agent": "Cloud-Init/%s"
                            % (version.version_string())
                        },
                    },
                    kwargs,
                )
                return m_response

        with mock.patch(M_PATH + "requests.Session") as m_session:
            error = requests.exceptions.HTTPError("broke")
            m_session.side_effect = [error, FakeSession()]
            # assert no retries and check_status == True
            with self.assertRaises(UrlError) as context_manager:
                response = read_file_or_url(url)
            self.assertEqual("broke", str(context_manager.exception))
            # assert default headers, method, url and allow_redirects True
            # Success on 2nd call with FakeSession
            response = read_file_or_url(url)
        self.assertEqual(m_response, response._response)


class TestReadFileOrUrlParameters:
    @mock.patch(M_PATH + "readurl")
    @pytest.mark.parametrize(
        "timeout", [1, 1.2, "1", (1, None), (1, 1), (None, None)]
    )
    def test_read_file_or_url_passes_params_to_readurl(
        self, m_readurl, timeout
    ):
        """read_file_or_url passes all params through to readurl."""
        url = "http://hostname/path"
        response = "This is my url content\n"
        m_readurl.return_value = response
        params = {
            "url": url,
            "timeout": timeout,
            "retries": 2,
            "headers": {"somehdr": "val"},
            "data": "data",
            "sec_between": 1,
            "ssl_details": {"cert_file": "/path/cert.pem"},
            "headers_cb": "headers_cb",
            "exception_cb": "exception_cb",
        }

        assert response == read_file_or_url(**params)
        params.pop("url")  # url is passed in as a positional arg
        assert m_readurl.call_args_list == [mock.call(url, **params)]

    @pytest.mark.parametrize(
        "readurl_timeout,request_timeout",
        [
            (-1, 0),
            ("-1", 0),
            (None, None),
            (1, 1.0),
            (1.2, 1.2),
            ("1", 1.0),
            ((1, None), (1, None)),
            ((1, 1), (1, 1)),
            ((None, None), (None, None)),
        ],
    )
    def test_readurl_timeout(self, readurl_timeout, request_timeout):
        url = "http://hostname/path"
        m_response = mock.MagicMock()

        class FakeSession(requests.Session):
            @classmethod
            def request(cls, **kwargs):
                expected_kwargs = {
                    "url": url,
                    "allow_redirects": True,
                    "method": "GET",
                    "headers": {
                        "User-Agent": "Cloud-Init/%s"
                        % (version.version_string())
                    },
                    "timeout": request_timeout,
                }
                if request_timeout is None:
                    expected_kwargs.pop("timeout")

                assert kwargs == expected_kwargs
                return m_response

        with mock.patch(
            M_PATH + "requests.Session", side_effect=[FakeSession()]
        ):
            response = read_file_or_url(url, timeout=readurl_timeout)

        assert response._response == m_response


class TestRetryOnUrlExc(CiTestCase):
    def test_do_not_retry_non_urlerror(self):
        """When exception is not UrlError return False."""
        myerror = IOError("something unexcpected")
        self.assertFalse(retry_on_url_exc(msg="", exc=myerror))

    def test_perform_retries_on_not_found(self):
        """When exception is UrlError with a 404 status code return True."""
        myerror = UrlError(
            cause=RuntimeError("something was not found"), code=NOT_FOUND
        )
        self.assertTrue(retry_on_url_exc(msg="", exc=myerror))

    def test_perform_retries_on_timeout(self):
        """When exception is a requests.Timout return True."""
        myerror = UrlError(cause=requests.Timeout("something timed out"))
        self.assertTrue(retry_on_url_exc(msg="", exc=myerror))


# vi: ts=4 expandtab
