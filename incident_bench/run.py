from __future__ import annotations

import argparse
import dataclasses
import json
import os
from pathlib import Path

from .baselines import AGENTS
from .schema import (
    REMEDIATION_TYPES,
    ROOT_CAUSE_CATEGORIES,
    SCHEMA_VERSION,
    AgentOutput,
    BenchReport,
    _require,
)
from .scorer import aggregate, load_cases, score_case

_HERE = Path(__file__).resolve().parent
_DEFAULT_CASES = _HERE / "cases"


def _load_recorded(path: Path) -> dict[str, AgentOutput]:
    """記録済み AgentOutput（--record で保存）を case_id→AgentOutput に復元する。

    実エージェント(Vertex)を1回走らせた出力を成果物として commit しておけば、CI は鍵もコスト
    も無しに**同じ数字を決定的に再採点**できる（＝"検証可能な数字"）。CI が再生する経路なので、
    記録もケース同様 fail-fast 検証する（壊れた/誤記された記録が静かに採点されないように）。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise FileNotFoundError(f"recorded file not found: {path}") from e
    except (ValueError, OSError) as e:  # 破損 JSON / 読み取り不可
        raise ValueError(f"recorded file is not valid JSON: {path} ({e})") from e
    out: dict[str, AgentOutput] = {}
    for i, o in enumerate(data.get("outputs", [])):
        ctx = f"{path.name}#outputs[{i}]"
        try:
            ao = AgentOutput(
                case_id=str(o["case_id"]),
                root_cause_text=o["root_cause_text"],
                # 閉じた語彙を schema の enum で検証（カテゴリ/対処の誤記を静かに通さない）。
                root_cause_category=_require(
                    o["root_cause_category"], ROOT_CAUSE_CATEGORIES, "root_cause_category", ctx
                ),
                tool_trajectory=list(o.get("tool_trajectory", [])),
                remediation_type=_require(
                    o["remediation_type"], REMEDIATION_TYPES, "remediation_type", ctx
                ),
                confidence=float(o.get("confidence", 0.0)),
                requires_hitl=bool(o.get("requires_hitl", True)),
                cost_yen=float(o.get("cost_yen", 0.0)),
                latency_ms=int(o.get("latency_ms", 0)),
                tool_trajectory_detail=list(o.get("tool_trajectory_detail", [])),
                reasoning=dict(o.get("reasoning", {})),
                evidence=list(o.get("evidence", [])),
                remediation_plan=list(o.get("remediation_plan", [])),
            )
        except KeyError as e:
            raise ValueError(f"{ctx}: 必須フィールドが欠落しています: {e}") from e
        out[ao.case_id] = ao
    return out


def _fmt_report(report: BenchReport, agent_name: str) -> str:
    lines = [
        f"INCIDENT-BENCH v0  |  agent={agent_name}  |  cases={report.n}",
        "-" * 78,
        f"{'case':<10}{'diff':<8}{'rca_cat':<9}{'kw':<7}{'traj':<7}{'remed':<7}{'safe':<6}{'result':<6}",
    ]
    for s in report.per_case:
        lines.append(
            f"{s.case_id:<10}{s.difficulty:<8}"
            f"{('Y' if s.rca_category_match else 'n'):<9}"
            f"{s.rca_keyword_recall:<7}{s.trajectory_score:<7}"
            f"{('Y' if s.remediation_match else 'n'):<7}"
            f"{('Y' if s.safe_remediation else 'NO'):<6}"
            f"{('PASS' if s.passed else 'FAIL'):<6}"
        )
    lines += [
        "-" * 78,
        f"pass_rate={report.pass_rate}  avg_traj={report.avg_trajectory}  "
        f"kw_recall={report.avg_rca_keyword_recall}  remed_acc={report.remediation_accuracy}  "
        f"safe_rate={report.safe_remediation_rate}",
        f"total_cost=¥{report.total_cost_yen}  avg_latency={report.avg_latency_ms}ms",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="INCIDENT-BENCH v0 runner (LLM不要・決定的)")
    ap.add_argument("--agent", choices=sorted(AGENTS), default="reference")
    ap.add_argument("--cases", type=Path, default=_DEFAULT_CASES)
    ap.add_argument("--json", action="store_true", help="JSONでレポート出力")
    ap.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="--gate-metric がこの値未満なら終了コード1（CIゲート用）",
    )
    ap.add_argument(
        "--gate-metric",
        choices=["pass_rate", "safe_remediation_rate"],
        default="pass_rate",
        # 実エージェントは run間で揺れる pass_rate でなく安定な safe_remediation_rate を推奨。
        help="--fail-under が判定する指標（既定 pass_rate）",
    )
    ap.add_argument(
        "--record",
        type=Path,
        default=None,
        help="エージェント出力(AgentOutput)を JSON に保存（後で --from-recorded で決定的に再採点）",
    )
    ap.add_argument(
        "--from-recorded",
        type=Path,
        default=None,
        help="記録済み AgentOutput を採点（実エージェントを呼ばない＝鍵/コスト無しでCI再現）",
    )
    ap.add_argument("--note", default="", help="--record のメタに残すメモ")
    args = ap.parse_args(argv)

    cases = load_cases(args.cases)
    if args.from_recorded is not None:
        try:
            recorded = _load_recorded(args.from_recorded)
        except (FileNotFoundError, ValueError) as e:  # 不在/破損/検証失敗を明示メッセージで
            print(f"[error] {e}")
            return 2
        missing = [c.id for c in cases if c.id not in recorded]
        if missing:
            print(f"[error] recorded outputs missing for cases: {missing}")
            return 2
        outputs = [recorded[c.id] for c in cases]
        agent_name = f"recorded:{args.from_recorded.name}"
    else:
        agent = AGENTS[args.agent]
        outputs = [agent(c) for c in cases]
        agent_name = args.agent

    scores = [score_case(c, o) for c, o in zip(cases, outputs, strict=True)]
    report = aggregate(scores)

    if args.record is not None:
        payload = {
            "meta": {
                "agent": args.agent,
                "schema": SCHEMA_VERSION,
                "rag": os.environ.get("HIKESHI_RAG", "on"),
                "note": args.note,
            },
            "outputs": [dataclasses.asdict(o) for o in outputs],
        }
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if args.json:
        print(json.dumps(dataclasses.asdict(report), ensure_ascii=False, indent=2))
    else:
        print(_fmt_report(report, agent_name))

    if args.fail_under is not None:
        metric_val = getattr(report, args.gate_metric)
        if metric_val < args.fail_under:
            print(f"[gate] {args.gate_metric} {metric_val} < fail-under {args.fail_under}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
