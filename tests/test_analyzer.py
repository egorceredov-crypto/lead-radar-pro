import asyncio
from app.ai.analyzer import analyze_message


def test_analyze_hot():
    res = asyncio.run(analyze_message('Ищу поставщика, нужен товар'))
    assert res['type'] == 'HOT'
    assert res['score'] >= 0.9


def test_analyze_price():
    res = asyncio.run(analyze_message('Сколько стоит этот товар?'))
    assert res['type'] == 'WARM'
    assert res['score'] >= 0.6


def test_analyze_not_lead():
    res = asyncio.run(analyze_message('Привет, как дела?'))
    assert res['type'] == 'NOT_LEAD' or res['score'] == 0.0
