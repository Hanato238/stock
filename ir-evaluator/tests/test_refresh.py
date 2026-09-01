"""マクロ更新パイプライン（macro/refresh.py）のオーケストレーションのテスト。

実際の fetch_*/report 関数は呼ばず、monkeypatch でスタブ化する
（実データ取得・LLM呼び出しは各モジュール側で個別にテスト済み）。
"""

import pytest

from macro import refresh


def test_run_step_success():
    result = refresh._run_step("x", lambda: "ok detail")
    assert result.ok
    assert result.name == "x"
    assert result.detail == "ok detail"


def test_run_step_catches_exception_and_marks_failed():
    def boom():
        raise RuntimeError("down")

    result = refresh._run_step("x", boom)
    assert not result.ok
    assert "RuntimeError" in result.detail
    assert "down" in result.detail


def _stub_all_steps(monkeypatch, *, overall_ok: bool = True):
    monkeypatch.setattr(refresh, "fetch_ci_di", lambda **k: ["ci"])
    monkeypatch.setattr(refresh, "fetch_jp_market", lambda **k: ["market"])
    monkeypatch.setattr(refresh, "fetch_us", lambda **k: ["us"])
    monkeypatch.setattr(refresh, "fetch_all_sectors", lambda: ["sector"])
    monkeypatch.setattr(refresh, "fetch_all_extra_sectors", lambda: ["sector_extra"])
    monkeypatch.setattr(refresh, "save_bundle", lambda series, filename: f"/tmp/{filename}")
    monkeypatch.setattr(refresh, "load_macro_bundle", lambda **k: object())
    monkeypatch.setattr(refresh, "save_report", lambda bundle: "/tmp/report.html")

    if overall_ok:
        monkeypatch.setattr(refresh, "fetch_overall", lambda **k: (["overall"], []))
        monkeypatch.setattr(refresh, "fetch_cpi", lambda **k: "cpi")
    else:
        def boom_overall(**k):
            raise RuntimeError("no api key")

        monkeypatch.setattr(refresh, "fetch_overall", boom_overall)


def test_main_exits_zero_when_all_steps_succeed(monkeypatch, capsys):
    _stub_all_steps(monkeypatch, overall_ok=True)
    refresh.main(argv=[])  # SystemExit を投げなければ成功（終了コード0相当）
    out = capsys.readouterr().out
    assert "[OK]   japan_market" in out
    assert "[OK]   overall" in out
    assert "[OK]   us" in out
    assert "[OK]   sectors" in out
    assert "[OK]   sectors_extra" in out
    assert "[OK]   report" in out


def test_main_continues_after_one_step_fails_and_exits_nonzero(monkeypatch, capsys):
    _stub_all_steps(monkeypatch, overall_ok=False)

    with pytest.raises(SystemExit) as exc_info:
        refresh.main(argv=[])

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "[FAIL] overall" in out
    assert "no api key" in out
    # 他のステップは overall の失敗に関わらず実行される。
    assert "[OK]   japan_market" in out
    assert "[OK]   us" in out
    assert "[OK]   sectors" in out
    assert "[OK]   sectors_extra" in out
    assert "[OK]   report" in out


def test_step_overall_skips_cpi_on_failure_but_still_succeeds(monkeypatch):
    monkeypatch.setattr(refresh, "fetch_overall", lambda **k: (["a", "b"], []))

    def boom_cpi(**k):
        raise RuntimeError("ESTAT_APP_ID 未設定")

    monkeypatch.setattr(refresh, "fetch_cpi", boom_cpi)
    monkeypatch.setattr(refresh, "save_bundle", lambda series, filename: f"/tmp/{filename}")

    detail = refresh._step_overall("2015-01-01")
    assert "CPIスキップ" in detail
    assert "2指標" in detail  # CPI 抜きの2指標のみ保存された


def test_step_overall_raises_when_nothing_fetched(monkeypatch):
    monkeypatch.setattr(refresh, "fetch_overall", lambda **k: ([], []))
    monkeypatch.setattr(refresh, "fetch_cpi", lambda **k: (_ for _ in ()).throw(RuntimeError("no key")))

    with pytest.raises(RuntimeError, match="取得成功した指標がありません"):
        refresh._step_overall("2015-01-01")
