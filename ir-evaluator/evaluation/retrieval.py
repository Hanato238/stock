"""2 段階 RAG 検索: 企業プロファイル → 審査辞典チャンクの関連証拠を集める。

Stage 1: 業種ターム（`evaluation.classify` が LLM で抽出した業種名の名詞句）から
         審査辞典のメタデータフィルタ（巻など）を解決する。`rag.mapping.resolve()` が
         None を返す間（industry_map の entries 未投入）はフィルタなし＝コレクション
         全体が対象（単一コレクション方式なので機能はする）。
Stage 2: 業種タームを主クエリに、与信観点の定型クエリと有報の要点（事業等のリスク・
         MD&A）を補助クエリにして辞典チャンクをベクトル検索し、重複を除いて距離順に
         上位を返す。審査辞典は業種別の財務指標テーブルが大半なので、散文よりも
         「◯◯製造業」のような業種名クエリのほうが精度が出る。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dictionary.chunk import split_text
from dictionary.embed import embed_texts
from dictionary.store import get_collection
from edinet.extract import ExtractedDocument
from edinet.industry import CompanyProfile, extract_section

# 有報の標準見出し。区間終端の検出にも使う。
_RISK_MARKERS = ("事業等のリスク",)
_MDA_MARKERS = (
    "経営者による財政状態、経営成績及びキャッシュ・フローの状況の分析",
    "財政状態、経営成績及びキャッシュ・フローの状況の分析",
    "業績等の概要",
)
_SECTION_END_MARKERS = (
    "経営方針、経営環境及び対処すべき課題等",
    "事業等のリスク",
    "経営者による財政状態、経営成績及びキャッシュ・フローの状況の分析",
    "重要な会計上の見積り",
    "経営上の重要な契約等",
    "研究開発活動",
    "設備の状況",
    "提出会社の状況",
)

# 業種タームごとに展開する定型クエリ（審査辞典に効く語彙で）。
_TERM_LENS = (
    "{term}",
    "{term}の経営指標・財務分析",
)
# 先頭タームだけで一度引く与信観点クエリ。
_HEAD_LENS = (
    "{term}の与信上の留意点・資金使途・資金需要",
    "{term}の業界動向・収益構造・経営課題",
)

# 1 クエリあたりの取得件数と、証拠として返す最大件数。
_PER_QUERY_K = 6
_DEFAULT_N_EVIDENCE = 14
# 同一辞典項目（code）から採用する最大チャンク数（証拠の多様性確保）。
_MAX_PER_CODE = 3

# 散文クエリ（有報のリスク/MD&A）は辞典（業種別財務指標テーブルが主体）に対して
# ノイズが混じりやすい。除外はせず、取得件数を絞り最終順位にペナルティを課すことで
# 業種タームクエリ由来の証拠を優先しつつ、他に候補が無い場合の保険として残す。
_PROSE_PER_QUERY_K = 3
_PROSE_DISTANCE_PENALTY = 0.08
# 有報セクションから補助クエリに回すチャンク数と長さ。
_QUERY_CHUNK_CHARS = 900
_MAX_SECTION_QUERIES = 2
# 事業の内容クエリの長さ（短く業種語を前に出したほうが辞典に効く）。
_BUSINESS_QUERY_CHARS = 500

# 有報【事業の内容】冒頭に定型で入るグループ構成の前置き（業種語を含まないノイズ）。
_BOILERPLATE_HEAD = re.compile(
    r"^.{0,200}?(?:により構成されて?(?:おり|います|いる)|で構成されて?(?:おり|います|いる)"
    r"|により構成される|から構成される)[、。]?"
)
_LEAD_SUBJECT = re.compile(r"^(?:当社(?:グループ)?(?:は|では)|当グループは|当社および連結子会社は)[、,]?")


@dataclass
class Evidence:
    """審査辞典から引いた 1 チャンク。"""

    code: str
    name: str
    section_name: str
    volume: int
    text: str
    distance: float
    matched_query: str

    @property
    def citation(self) -> str:
        return f"{self.code} {self.name}（{self.section_name}）"


@dataclass
class RetrievalResult:
    evidence: list[Evidence]
    queries: list[str]
    where: dict | None
    industry_label: str
    risk_section: str
    mda_section: str


def resolve_filter(profile: CompanyProfile) -> dict | None:
    """業種シードから審査辞典のメタデータフィルタを解決する（現状はほぼ None）。"""
    try:
        from rag.mapping import resolve
    except ImportError:
        return None
    hit = resolve(profile.industry_seed_text, tse33=None)
    if hit is None:
        return None
    vol = hit.entry.dictionary_volume
    return {"volume": vol} if vol else None


def _first_section(doc: ExtractedDocument, markers: tuple[str, ...]) -> str:
    for marker in markers:
        text = extract_section(doc, marker, [m for m in _SECTION_END_MARKERS if m != marker])
        if text:
            return text
    return ""


def _clean_business_text(text: str) -> str:
    """【事業の内容】から定型のグループ構成前置き・主語を落として業種語を前に出す。"""
    t = " ".join(text.split()).strip()
    t = _BOILERPLATE_HEAD.sub("", t).strip()
    t = _LEAD_SUBJECT.sub("", t).strip()
    # 「報告セグメントは…」以降の管理会計的な記述は業種検索にはノイズ。
    t = re.split(r"報告セグメント", t)[0].strip()
    return t or text.strip()


# ヒューリスティック抽出が失敗している兆候（この語を含む「業種ラベル」は使わない）。
_BAD_LABEL_MARKERS = ("構成", "子会社", "関連会社", "株式会社", "有限会社")


def _industry_label(profile: CompanyProfile) -> str:
    """クエリに差し込む業種ラベル（`classify` 失敗時のフォールバック）。

    事業の内容から業種語を含む短句を取る。定型のグループ構成文しか取れなかった
    場合は空文字を返し、呼び出し側で事業の内容そのものをクエリにさせる。
    """
    cleaned = _clean_business_text(profile.business_content)
    if not cleaned:
        return ""
    head = re.split(r"[。\n]", cleaned)[0].strip()
    if len(head) > 40:
        head = head.split("、")[0].strip()
    if not (4 <= len(head) <= 60):
        return ""
    if any(m in head for m in _BAD_LABEL_MARKERS):
        return ""
    return head


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        key = x.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _section_chunks(risk_section: str, mda_section: str) -> list[str]:
    """有報のリスク/MD&Aセクションを、補助クエリ用チャンクに分割する。"""
    chunks: list[str] = []
    for section in (risk_section, mda_section):
        if section:
            chunks.extend(
                split_text(section, chunk_size=_QUERY_CHUNK_CHARS)[:_MAX_SECTION_QUERIES]
            )
    return chunks


def _build_queries(
    terms: list[str],
    profile: CompanyProfile,
    risk_section: str,
    mda_section: str,
) -> list[str]:
    queries: list[str] = []
    for term in terms:
        queries.extend(t.format(term=term) for t in _TERM_LENS)

    head = terms[0] if terms else (profile.filer_name or "当社")
    queries.extend(t.format(term=head) for t in _HEAD_LENS)

    # 業種タームが LLM から取れなかった場合の保険として事業の内容も 1 本入れる。
    if not terms and profile.business_content:
        queries.append(_clean_business_text(profile.business_content)[:_BUSINESS_QUERY_CHARS])

    queries.extend(_section_chunks(risk_section, mda_section))

    return _dedupe(queries)


def retrieve_evidence(
    profile: CompanyProfile,
    doc: ExtractedDocument,
    *,
    industry_terms: list[str] | None = None,
    n_evidence: int = _DEFAULT_N_EVIDENCE,
) -> RetrievalResult:
    """プロファイル＋有報から審査辞典の関連チャンクを集める。

    Args:
        industry_terms: `evaluation.classify.classify_industry()` が返す業種名の
            名詞句。省略時は【事業の内容】からの正規表現ヒューリスティックにフォールバック。
    """
    terms = list(industry_terms or [])
    if not terms:
        label = _industry_label(profile)
        if label:
            terms = [label]

    risk_section = _first_section(doc, _RISK_MARKERS)
    mda_section = _first_section(doc, _MDA_MARKERS)
    queries = _build_queries(terms, profile, risk_section, mda_section)
    prose_queries = set(_section_chunks(risk_section, mda_section))
    where = resolve_filter(profile)

    collection = get_collection()
    query_embeddings = embed_texts(queries, task_type="RETRIEVAL_QUERY")

    hits: list[tuple[str, list[str], list[dict], list[float]]] = []
    for query, emb in zip(queries, query_embeddings):
        k = _PROSE_PER_QUERY_K if query in prose_queries else _PER_QUERY_K
        res = collection.query(
            query_embeddings=[emb],
            n_results=k,
            where=where or None,
        )
        hits.append((query, res["documents"][0], res["metadatas"][0], res["distances"][0]))

    evidence = _select_evidence(hits, prose_queries, n_evidence)
    return RetrievalResult(
        evidence=evidence,
        queries=queries,
        where=where,
        industry_label=" / ".join(terms) if terms else (profile.filer_name or ""),
        risk_section=risk_section,
        mda_section=mda_section,
    )


def _select_evidence(
    hits: list[tuple[str, list[str], list[dict], list[float]]],
    prose_queries: set[str],
    n_evidence: int,
) -> list[Evidence]:
    """クエリ別ヒットを集約し、同一項目の偏りを抑えつつ上位を選ぶ。

    同一チャンクは実際の最小距離で保持する（`Evidence.distance` はレポート表示用に
    正確な値を残す）。最終順位付けのみ、そのチャンクの最良ヒットが散文クエリ
    （有報リスク/MD&A）由来なら `_PROSE_DISTANCE_PENALTY` を加えて後ろへ回す。
    """
    # chunk id（code-index）→ 最良ヒットで集約する。
    best: dict[str, Evidence] = {}
    for query, documents, metadatas, distances in hits:
        for doc_text, meta, dist in zip(documents, metadatas, distances):
            chunk_id = f"{meta['code']}-{meta.get('chunk_index', 0)}"
            if chunk_id in best and best[chunk_id].distance <= dist:
                continue
            best[chunk_id] = Evidence(
                code=str(meta["code"]),
                name=str(meta.get("name", "")),
                section_name=str(meta.get("section_name", "")),
                volume=int(meta.get("volume", 0) or 0),
                text=doc_text,
                distance=float(dist),
                matched_query=query,
            )

    def _rank_key(ev: Evidence) -> float:
        penalty = _PROSE_DISTANCE_PENALTY if ev.matched_query in prose_queries else 0.0
        return ev.distance + penalty

    # ペナルティ込みの順位で採用しつつ、同一項目からの偏りを _MAX_PER_CODE で抑える。
    per_code: dict[str, int] = {}
    evidence: list[Evidence] = []
    for ev in sorted(best.values(), key=_rank_key):
        if per_code.get(ev.code, 0) >= _MAX_PER_CODE:
            continue
        per_code[ev.code] = per_code.get(ev.code, 0) + 1
        evidence.append(ev)
        if len(evidence) >= n_evidence:
            break
    return evidence
