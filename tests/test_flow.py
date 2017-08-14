# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import concurrent.futures
import json
import os

import mock
import pytest
import requests
from six.moves import urllib

from google_auth_oauthlib import flow

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
CLIENT_SECRETS_FILE = os.path.join(DATA_DIR, 'client_secrets.json')

with open(CLIENT_SECRETS_FILE, 'r') as fh:
    CLIENT_SECRETS_INFO = json.load(fh)


class TestFlow(object):
    def test_from_client_secrets_file(self):
        instance = flow.Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE, scopes=mock.sentinel.scopes)
        assert instance.client_config == CLIENT_SECRETS_INFO['web']
        assert (instance.oauth2session.client_id ==
                CLIENT_SECRETS_INFO['web']['client_id'])
        assert instance.oauth2session.scope == mock.sentinel.scopes

    def test_from_client_config_installed(self):
        client_config = {'installed': CLIENT_SECRETS_INFO['web']}
        instance = flow.Flow.from_client_config(
            client_config, scopes=mock.sentinel.scopes)
        assert instance.client_config == client_config['installed']
        assert (instance.oauth2session.client_id ==
                client_config['installed']['client_id'])
        assert instance.oauth2session.scope == mock.sentinel.scopes

    def test_from_client_config_bad_format(self):
        with pytest.raises(ValueError):
            flow.Flow.from_client_config({}, scopes=mock.sentinel.scopes)

    @pytest.fixture
    def instance(self):
        yield flow.Flow.from_client_config(
            CLIENT_SECRETS_INFO, scopes=mock.sentinel.scopes)

    def test_redirect_uri(self, instance):
        instance.redirect_uri = mock.sentinel.redirect_uri
        assert (instance.redirect_uri ==
                instance.oauth2session.redirect_uri ==
                mock.sentinel.redirect_uri)

    def test_authorization_url(self, instance):
        scope = 'scope_one'
        instance.oauth2session.scope = [scope]
        authorization_url_patch = mock.patch.object(
            instance.oauth2session, 'authorization_url',
            wraps=instance.oauth2session.authorization_url)

        with authorization_url_patch as authorization_url_spy:
            url, _ = instance.authorization_url(prompt='consent')

            assert CLIENT_SECRETS_INFO['web']['auth_uri'] in url
            assert scope in url
            authorization_url_spy.assert_called_with(
                CLIENT_SECRETS_INFO['web']['auth_uri'],
                access_type='offline',
                prompt='consent')

    def test_fetch_token(self, instance):
        fetch_token_patch = mock.patch.object(
            instance.oauth2session, 'fetch_token', autospec=True,
            return_value=mock.sentinel.token)

        with fetch_token_patch as fetch_token_mock:
            token = instance.fetch_token(code=mock.sentinel.code)

            assert token == mock.sentinel.token
            fetch_token_mock.assert_called_with(
                CLIENT_SECRETS_INFO['web']['token_uri'],
                client_secret=CLIENT_SECRETS_INFO['web']['client_secret'],
                code=mock.sentinel.code)

    def test_credentials(self, instance):
        instance.oauth2session.token = {
            'access_token': mock.sentinel.access_token,
            'refresh_token': mock.sentinel.refresh_token
        }

        credentials = instance.credentials

        assert credentials.token == mock.sentinel.access_token
        assert credentials._refresh_token == mock.sentinel.refresh_token
        assert (credentials._client_id ==
                CLIENT_SECRETS_INFO['web']['client_id'])
        assert (credentials._client_secret ==
                CLIENT_SECRETS_INFO['web']['client_secret'])
        assert (credentials._token_uri ==
                CLIENT_SECRETS_INFO['web']['token_uri'])

    def test_authorized_session(self, instance):
        instance.oauth2session.token = {
            'access_token': mock.sentinel.access_token,
            'refresh_token': mock.sentinel.refresh_token
        }

        session = instance.authorized_session()

        assert session.credentials.token == mock.sentinel.access_token


class TestInstalledAppFlow(object):
    SCOPES = ['email', 'profile']
    REDIRECT_REQUEST_PATH = '/?code=code&state=state'

    @pytest.fixture
    def instance(self):
        yield flow.InstalledAppFlow.from_client_config(
            CLIENT_SECRETS_INFO, scopes=self.SCOPES)

    @pytest.fixture
    def mock_fetch_token(self, instance):
        def set_token(*args, **kwargs):
            instance.oauth2session.token = {
                'access_token': mock.sentinel.access_token,
                'refresh_token': mock.sentinel.refresh_token
            }

        fetch_token_patch = mock.patch.object(
            instance.oauth2session, 'fetch_token', autospec=True,
            side_effect=set_token)

        with fetch_token_patch as fetch_token_mock:
            yield fetch_token_mock

    @mock.patch('google_auth_oauthlib.flow.input', autospec=True)
    def test_run_console(self, input_mock, instance, mock_fetch_token):
        input_mock.return_value = mock.sentinel.code

        credentials = instance.run_console()

        assert credentials.token == mock.sentinel.access_token
        assert credentials._refresh_token == mock.sentinel.refresh_token

        mock_fetch_token.assert_called_with(
            CLIENT_SECRETS_INFO['web']['token_uri'],
            client_secret=CLIENT_SECRETS_INFO['web']['client_secret'],
            code=mock.sentinel.code)

    @pytest.mark.webtest
    @mock.patch('google_auth_oauthlib.flow.webbrowser', autospec=True)
    def test_run_local_server(
            self, webbrowser_mock, instance, mock_fetch_token):
        auth_redirect_url = urllib.parse.urljoin(
            'http://localhost:8080',
            self.REDIRECT_REQUEST_PATH)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(instance.run_local_server)

            while not future.done():
                try:
                    requests.get(auth_redirect_url)
                except requests.ConnectionError:  # pragma: NO COVER
                    pass

            credentials = future.result()

        assert credentials.token == mock.sentinel.access_token
        assert credentials._refresh_token == mock.sentinel.refresh_token
        assert webbrowser_mock.open.called

        expected_auth_response = auth_redirect_url.replace('http', 'https')
        mock_fetch_token.assert_called_with(
            CLIENT_SECRETS_INFO['web']['token_uri'],
            client_secret=CLIENT_SECRETS_INFO['web']['client_secret'],
            authorization_response=expected_auth_response)

    @mock.patch('google_auth_oauthlib.flow.webbrowser', autospec=True)
    @mock.patch('wsgiref.simple_server.make_server', autospec=True)
    def test_run_local_server_no_browser(
            self, make_server_mock, webbrowser_mock, instance,
            mock_fetch_token):

        def assign_last_request_uri(host, port, wsgi_app, **kwargs):
            wsgi_app.last_request_uri = self.REDIRECT_REQUEST_PATH
            return mock.Mock()

        make_server_mock.side_effect = assign_last_request_uri

        instance.run_local_server(open_browser=False)

        assert not webbrowser_mock.open.called
