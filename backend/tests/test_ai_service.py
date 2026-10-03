import pytest
from flask import Flask

from modules.ai import service

app = Flask(__name__)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_generate_requests_json_and_returns_model_text(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured['url'] = url
        captured['kwargs'] = kwargs
        return _Response({
            'candidates': [{
                'content': {'parts': [{'text': '{"explanation":"Works"}'}]},
                'finishReason': 'STOP',
            }]
        })

    monkeypatch.setattr(service, '_get_client', lambda: 'test-key')
    monkeypatch.setattr(service, '_get_model', lambda: 'test-model')
    monkeypatch.setattr(service.requests, 'post', fake_post)

    with app.test_request_context('/'):
        result = service._generate(
            'Explain this bug',
            max_tokens=2048,
            response_mime_type='application/json',
        )

    assert result == '{"explanation":"Works"}'
    assert captured['kwargs']['json']['generationConfig'] == {
        'maxOutputTokens': 2048,
        'responseMimeType': 'application/json',
    }


def test_generate_rejects_a_candidate_without_text(monkeypatch):
    monkeypatch.setattr(service, '_get_client', lambda: 'test-key')
    monkeypatch.setattr(service, '_get_model', lambda: 'test-model')
    monkeypatch.setattr(
        service.requests,
        'post',
        lambda *args, **kwargs: _Response({
            'candidates': [{
                'content': {},
                'finishReason': 'MAX_TOKENS',
            }]
        }),
    )

    with app.test_request_context('/'):
        with pytest.raises(Exception, match='empty response \\(MAX_TOKENS\\)'):
            service._generate('Explain this bug', max_tokens=10)


def test_generate_retries_temporary_provider_failures(monkeypatch):
    attempts = {'count': 0}

    class _TemporaryFailure(_Response):
        status_code = 503

        def raise_for_status(self):
            error = service.requests.HTTPError('service unavailable')
            error.response = self
            raise error

    def fake_post(*args, **kwargs):
        attempts['count'] += 1
        if attempts['count'] < 3:
            return _TemporaryFailure({})
        return _Response({
            'candidates': [{
                'content': {'parts': [{'text': 'recovered'}]},
                'finishReason': 'STOP',
            }]
        })

    monkeypatch.setattr(service, '_get_client', lambda: 'test-key')
    monkeypatch.setattr(service, '_get_model', lambda: 'test-model')
    monkeypatch.setattr(service.requests, 'post', fake_post)
    monkeypatch.setattr(service.time, 'sleep', lambda _: None)

    with app.test_request_context('/'):
        assert service._generate('Explain this bug', max_tokens=10) == 'recovered'

    assert attempts['count'] == 3
