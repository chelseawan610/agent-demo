from travel_agent.app import cli


class _FakeStream:
    def __init__(self, *, reject: bool = False):
        self.calls = []
        self.reject = reject

    def reconfigure(self, **kwargs):
        if self.reject:
            raise ValueError("not reconfigurable")
        self.calls.append(kwargs)


def test_cli_configures_all_stdio_streams_as_utf8(monkeypatch):
    streams = [_FakeStream(), _FakeStream(), _FakeStream(reject=True)]
    monkeypatch.setattr(cli.sys, "stdin", streams[0])
    monkeypatch.setattr(cli.sys, "stdout", streams[1])
    monkeypatch.setattr(cli.sys, "stderr", streams[2])

    cli._configure_stdio_encoding()

    assert streams[0].calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert streams[1].calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert streams[2].calls == []
