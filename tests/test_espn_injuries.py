import requests

from lib.espn_injuries import (
    INJURY_COLUMNS,
    _request_json_with_retries,
    fetch_espn_injuries,
    normalize_availability_classification,
)

# ---------------------------------------------------------------------------
# Status classification vocabulary - the exact product decision under test:
# Questionable/Doubtful are conditional, NEVER confirmed_unavailable.
# ---------------------------------------------------------------------------
def test_out_is_confirmed_unavailable():
    assert normalize_availability_classification("Out") == ("confirmed_unavailable", True)


def test_ir_is_confirmed_unavailable():
    assert normalize_availability_classification("IR") == ("confirmed_unavailable", True)
    assert normalize_availability_classification("Injured Reserve") == ("confirmed_unavailable", True)


def test_suspended_is_confirmed_unavailable():
    assert normalize_availability_classification("Suspended") == ("confirmed_unavailable", True)


def test_questionable_is_conditional_not_confirmed_unavailable():
    classification, is_confirmed = normalize_availability_classification("Questionable")
    assert classification == "conditional"
    assert is_confirmed is False


def test_doubtful_is_conditional_not_confirmed_unavailable():
    classification, is_confirmed = normalize_availability_classification("Doubtful")
    assert classification == "conditional"
    assert is_confirmed is False


def test_healthy_and_probable_and_active_are_available():
    for status in ("Healthy", "Probable", "Active"):
        assert normalize_availability_classification(status) == ("available", False)


def test_no_status_at_all_is_available_not_unknown():
    # ESPN listing no injury entry for a player means healthy, not "unknown"
    # - "unknown" is reserved for source failure / unrecognized strings.
    assert normalize_availability_classification(None) == ("available", False)
    assert normalize_availability_classification("") == ("available", False)
    assert normalize_availability_classification("   ") == ("available", False)


def test_unrecognized_status_is_unknown_never_assumed():
    classification, is_confirmed = normalize_availability_classification("Some New ESPN Wording")
    assert classification == "unknown"
    assert is_confirmed is False


def test_classification_is_case_and_whitespace_insensitive():
    assert normalize_availability_classification("  out  ") == ("confirmed_unavailable", True)
    assert normalize_availability_classification("QUESTIONABLE") == ("conditional", False)


# ---------------------------------------------------------------------------
# HTTP retry/backoff behavior
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status_code, payload=None, raise_json_error=False):
        self.status_code = status_code
        self._payload = payload
        self._raise_json_error = raise_json_error

    def json(self):
        if self._raise_json_error:
            raise ValueError("invalid json")
        return self._payload


class _FakeSession:
    """Records calls and returns a scripted sequence of responses/exceptions."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_retries_on_connection_error_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)  # don't actually wait in tests
    session = _FakeSession([
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.Timeout("boom again"),
        _FakeResponse(200, {"ok": True}),
    ])
    result, error = _request_json_with_retries(session, "https://example.test", max_retries=3, backoff_seconds=0)
    assert result == {"ok": True}
    assert error is None
    assert session.calls == 3


def test_gives_up_after_max_retries_and_returns_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    session = _FakeSession([
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ConnectionError("boom"),
    ])
    result, error = _request_json_with_retries(session, "https://example.test", max_retries=3, backoff_seconds=0)
    assert result is None
    assert "ConnectionError" in error
    assert session.calls == 3


def test_5xx_is_retried_but_4xx_is_not(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    session = _FakeSession([_FakeResponse(404)])
    result, error = _request_json_with_retries(session, "https://example.test", max_retries=3, backoff_seconds=0)
    assert result is None
    assert error == "HTTP 404"
    assert session.calls == 1  # not retried


def test_5xx_is_retried_until_success(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    session = _FakeSession([_FakeResponse(503), _FakeResponse(200, {"ok": True})])
    result, error = _request_json_with_retries(session, "https://example.test", max_retries=3, backoff_seconds=0)
    assert result == {"ok": True}
    assert session.calls == 2


def test_invalid_json_on_200_is_not_retried_and_reports_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    session = _FakeSession([_FakeResponse(200, raise_json_error=True)])
    result, error = _request_json_with_retries(session, "https://example.test", max_retries=3, backoff_seconds=0)
    assert result is None
    assert "Invalid JSON" in error
    assert session.calls == 1


# ---------------------------------------------------------------------------
# fetch_espn_injuries - schema validation, per-team failure isolation, no
# silent healthy fallback
# ---------------------------------------------------------------------------
def _teams_payload(team_specs):
    return {
        "sports": [{"leagues": [{"teams": [
            {"team": {"id": str(i + 1), "abbreviation": abbrev}} for i, abbrev in enumerate(team_specs)
        ]}]}]
    }


def _roster_payload(players):
    return {"athletes": [{"items": players}]}


def test_fetch_returns_no_success_and_empty_df_when_teams_list_fetch_fails(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    session = _FakeSession([
        requests.exceptions.ConnectionError("down"),
        requests.exceptions.ConnectionError("down"),
        requests.exceptions.ConnectionError("down"),
    ])
    injuries_df, meta = fetch_espn_injuries(session=session)
    assert injuries_df.empty
    assert list(injuries_df.columns) == INJURY_COLUMNS
    assert meta["source_success"] is False
    assert meta["error"] is not None


def test_fetch_returns_no_success_on_malformed_teams_schema(monkeypatch):
    session = _FakeSession([_FakeResponse(200, {"unexpected": "shape"})])
    injuries_df, meta = fetch_espn_injuries(session=session)
    assert injuries_df.empty
    assert meta["source_success"] is False
    assert "schema" in meta["error"].lower()


def test_fetch_isolates_a_single_failed_team_without_losing_others(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    teams = _teams_payload(["KC", "BUF"])
    session = _FakeSession([
        _FakeResponse(200, teams),  # teams list
        requests.exceptions.ConnectionError("down"),  # KC roster x3 retries
        requests.exceptions.ConnectionError("down"),
        requests.exceptions.ConnectionError("down"),
        _FakeResponse(200, _roster_payload([  # BUF roster succeeds
            {"id": "111", "displayName": "Josh Allen", "position": {"abbreviation": "QB"}, "injuries": []},
        ])),
    ])
    injuries_df, meta = fetch_espn_injuries(session=session)
    assert meta["source_success"] is False  # not every team succeeded
    assert meta["teams_succeeded"] == 1
    assert meta["failed_team_abbrevs"] == ["KC"]
    # BUF's player is still present - one team's failure doesn't wipe others.
    assert list(injuries_df["espn_team_abbrev"]) == ["BUF"]
    assert injuries_df.iloc[0]["availability_classification"] == "available"


def test_fetch_skips_player_rows_missing_id_or_name(monkeypatch):
    teams = _teams_payload(["KC"])
    session = _FakeSession([
        _FakeResponse(200, teams),
        _FakeResponse(200, _roster_payload([
            {"id": "1", "displayName": "Has Both", "position": {"abbreviation": "WR"}, "injuries": []},
            {"id": None, "displayName": "No Id Guy", "position": {"abbreviation": "WR"}, "injuries": []},
            {"id": "2", "displayName": None, "position": {"abbreviation": "WR"}, "injuries": []},
        ])),
    ])
    injuries_df, meta = fetch_espn_injuries(session=session)
    assert len(injuries_df) == 1
    assert injuries_df.iloc[0]["player_display_name"] == "Has Both"
    assert meta["skipped_player_rows"] == 2


def test_fetch_success_requires_every_team_to_succeed():
    teams = _teams_payload(["KC"])
    session = _FakeSession([
        _FakeResponse(200, teams),
        _FakeResponse(200, _roster_payload([
            {"id": "1", "displayName": "Healthy Guy", "position": {"abbreviation": "RB"}, "injuries": []},
        ])),
    ])
    injuries_df, meta = fetch_espn_injuries(session=session)
    assert meta["source_success"] is True
    assert meta["teams_succeeded"] == 1
    assert meta["failed_team_abbrevs"] == []


def test_fetch_captures_injury_designation_and_classification():
    teams = _teams_payload(["KC"])
    session = _FakeSession([
        _FakeResponse(200, teams),
        _FakeResponse(200, _roster_payload([
            {
                "id": "1", "displayName": "Hurt Guy", "position": {"abbreviation": "RB"},
                "injuries": [{"status": "Out", "type": "Ankle", "details": {"detail": "High ankle sprain"}}],
            },
        ])),
    ])
    injuries_df, _ = fetch_espn_injuries(session=session)
    row = injuries_df.iloc[0]
    assert row["source_status"] == "Out"
    assert row["availability_classification"] == "confirmed_unavailable"
    assert row["is_confirmed_unavailable"] == True
    assert row["injury_type"] == "Ankle"
    assert row["injury_details"] == "High ankle sprain"
